import os
from collections import defaultdict
from io import BytesIO
from wtforms import StringField, IntegerField, TextAreaField, FieldList, FormField, SubmitField
from flask_wtf import FlaskForm
from wtforms.validators import DataRequired
from werkzeug.security import generate_password_hash
import pandas as pd
from flask import render_template, request, redirect, url_for, jsonify, flash, send_file, session
from flask_paginate import Pagination, get_page_parameter
from sqlalchemy import func
from sqlalchemy import or_
from werkzeug.security import check_password_hash
from __init__ import create_app, db  # Import db from __init__.py
from models import SKU, StockHistory, Racking, PackSize, Order, OrderItem
from flask_login import LoginManager,login_user, logout_user, login_required, current_user
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from fpdf import FPDF, XPos, YPos
import tempfile
from datetime import date
from models import User, Stock
from datetime import datetime
from sqlalchemy import extract
from models import DailyRackCount
import pytz
import logging
import re
from flask import request, jsonify, render_template
from sqlalchemy import or_
from flask_login import login_required, current_user
from calendar import monthrange

app = create_app()

login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Route for the index page
@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/inventory-sync')
def inventory_sync():
    return render_template('inventory_sync.html')

@app.route('/preview-stock', methods=['POST'])
@login_required
def preview_stock():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    try:
        # Read the uploaded Excel file using pandas
        df = pd.read_excel(file)

        # Define the required columns
        required_columns = ['Material Number', 'Quantity', 'Batch Number', 'Shipment Number', 'Racking Number', 'Remarks']

        # Check if all required columns are present in the Excel file
        if not all(column in df.columns for column in required_columns):
            missing_columns = [column for column in required_columns if column not in df.columns]
            return jsonify({'error': f'Missing or incorrectly named columns: {", ".join(missing_columns)}'}), 400

        stock_list = []

        # Process the DataFrame
        for _, row in df.iterrows():
            # Safely get values from the row
            material_number = row.get('Material Number', None)
            quantity = row.get('Quantity', 0)  # Default to 0 if quantity is NaN
            batch_number = row.get('Batch Number', '')
            shipment_number = row.get('Shipment Number', '')
            racking_number = row.get('Racking Number', '')
            remarks = row.get('Remarks', '')

            # Ensure material_number is not NaN or None
            if pd.isna(material_number) or material_number is None:
                return jsonify({'error': 'Material number is missing or invalid'}), 400

            # Ensure quantity is numeric and not NaN, set to 0 if invalid
            if pd.isna(quantity) or not isinstance(quantity, (int, float)):
                quantity = 0  # Default to 0 if quantity is invalid

            # Handle any NaN values in batch_number, shipment_number, racking_number, and remarks
            batch_number = batch_number if pd.notna(batch_number) else ''
            shipment_number = shipment_number if pd.notna(shipment_number) else ''
            racking_number = racking_number if pd.notna(racking_number) else ''
            remarks = remarks if pd.notna(remarks) else ''

            # Find SKU from the material number
            sku = SKU.query.filter_by(material_number=material_number).first()
            if sku:
                product_description = sku.product_description
            else:
                product_description = 'Unknown'

            # Add each row to the stock list for preview
            stock_list.append({
                'material_number': str(material_number),  # Convert material_number to string for JSON compatibility
                'product_description': product_description,
                'quantity': int(quantity),  # Convert quantity to int or float for JSON compatibility
                'batch_number': str(batch_number),  # Ensure all string fields are converted to strings
                'shipment_number': str(shipment_number),
                'racking_number': str(racking_number),
                'remarks': str(remarks)
            })

        # Return the stock list for frontend preview
        return jsonify({'stock_list': stock_list}), 200

    except Exception as e:
        return jsonify({'error': f'Error processing file: {str(e)}'}), 500

@app.route('/export-stocks/excel', methods=['GET'])
@login_required
def export_stocks_excel():
    try:
        # Fetch all stock entries from the database without pagination
        stocks = Stock.query.join(SKU).all()

        # Prepare data for Excel
        stock_data = [{
            'Material Number': stock.sku.material_number,
            'Product Description': stock.sku.product_description,
            'Quantity': stock.quantity,
            'Batch Number': stock.batch_number,
            'Shipment Number': stock.shipment_number,
            'Racking Number': stock.racking_number,
            'Remarks': stock.remarks
        } for stock in stocks]

        # Convert the data into a pandas DataFrame
        df = pd.DataFrame(stock_data)

        # Write the DataFrame to an Excel file in memory
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Stock List')

        # Set the pointer to the beginning of the stream
        output.seek(0)

        # Return the Excel file as an attachment
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True, download_name='stock_list.xlsx')

    except Exception as e:
        print(f"Error occurred: {str(e)}")
        return "Error exporting data", 500

@app.route('/submit-stock', methods=['POST'])
@login_required
def submit_stock():
    stock_list = request.json.get('stock_list')

    if not stock_list or len(stock_list) == 0:
        return jsonify({'error': 'No stock data provided'}), 400

    for stock_data in stock_list:
        material_number = stock_data.get('material_number')
        quantity = stock_data.get('quantity')
        batch_number = stock_data.get('batch_number')
        shipment_number = stock_data.get('shipment_number')
        racking_number = stock_data.get('racking_number')
        remarks = stock_data.get('remarks')

        # Find the SKU by material number
        sku = SKU.query.filter_by(material_number=material_number).first()

        if not sku:
            return jsonify({'error': f'SKU with material number {material_number} not found'}), 404

        # Check for existing stock with the same attributes
        existing_stock = Stock.query.filter_by(
            sku_id=sku.id,
            batch_number=batch_number,
            shipment_number=shipment_number,
            racking_number=racking_number,
            remarks=remarks
        ).first()

        if existing_stock:
            # Update the existing stock quantity
            existing_stock.quantity += quantity

            # Log the change in the stock history
            stock_history = StockHistory(
                stock_id=existing_stock.id,
                change_type='GR',  # Goods Receiving
                quantity=quantity,  # Log the added quantity
                username = current_user.username
            )
            db.session.add(stock_history)
        else:
            # Create a new Stock entry
            new_stock = Stock(
                sku_id=sku.id,
                quantity=quantity,
                batch_number=batch_number,
                shipment_number=shipment_number,
                racking_number=racking_number,
                remarks=remarks
            )

            # Add the new stock to the session
            db.session.add(new_stock)
            db.session.flush()  # Flush to get the new_stock.id

            # Create a StockHistory entry for this stock
            stock_history = StockHistory(
                stock_id=new_stock.id,  # Use the new stock's ID
                change_type='GR',  # Goods Receiving
                quantity=quantity,
                username=current_user.username # Log the added quantity
            )

            # Add the stock history to the session
            db.session.add(stock_history)

    db.session.commit()  # Commit all stock entries and history to the database

    return jsonify({'message': 'Stock items submitted successfully!'}), 200

@app.route('/save-stock/<int:stock_id>', methods=['POST'])
@login_required
def save_stock(stock_id):
    try:
        print(f"Attempting to save stock with ID: {stock_id}")

        # Parse the request data
        data = request.get_json()
        new_remarks = data.get('remarks')

        # Fetch the stock to be updated
        stock = Stock.query.get(stock_id)
        if not stock:
            return jsonify({'error': 'Stock not found'}), 404

        # Update stock remarks
        stock.remarks = new_remarks
        db.session.add(stock)

        # Record the change in the stock history
        stock_history = StockHistory(
            stock_id=stock.id,
            change_type=f'Updated remarks to {new_remarks}',
            quantity=stock.quantity,  # Record the quantity
            username=current_user.username
        )
        db.session.add(stock_history)

        # Commit the changes to the database
        db.session.commit()

        return jsonify({'success': True}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Error updating stock: {str(e)}'}), 500

@app.route('/upload-sku-excel', methods=['GET', 'POST'])
@login_required
def upload_sku_excel():
    if request.method == 'POST':
        file = request.files['file']

        # Check if a file is selected
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('add_sku_html'))

        # Ensure uploads and logs directory exist
        uploads_dir = os.path.join(os.getcwd(), 'uploads')
        logs_dir = os.path.join(os.getcwd(), 'logs')
        if not os.path.exists(uploads_dir):
            os.makedirs(uploads_dir)
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir)

        # Save the file temporarily
        filename = file.filename
        filepath = os.path.join(uploads_dir, filename)
        file.save(filepath)

        # Log file for unsuccessful uploads
        from datetime import datetime
        log_filename = f"unsuccessful_sku_uploads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        log_filepath = os.path.join(logs_dir, log_filename)

        try:
            # Read the Excel file using pandas
            df = pd.read_excel(filepath)

            # Fetch all pack sizes from the database dynamically
            pack_sizes = PackSize.query.all()
            pack_size_map = {pack.size: pack.id for pack in pack_sizes}

            success_count = 0
            error_count = 0

            # Open the log file to record unsuccessful SKUs
            with open(log_filepath, 'w') as log_file:
                log_file.write('Unsuccessful SKU Uploads Log\n')
                log_file.write('=============================\n')

                # Loop through each row and add SKUs to the database
                for index, row in df.iterrows():
                    material_number = str(row['Material Number'])
                    product_description = str(row['Product Description'])
                    weight = row['Weight']  # Get the weight from the Excel file

                    # Match the pack size from the product description
                    matched_pack_size = None
                    for pack_size_str in pack_size_map.keys():
                        if pack_size_str in product_description:
                            matched_pack_size = pack_size_map[pack_size_str]
                            break

                    if not matched_pack_size:
                        log_file.write(
                            f"Material Number: {material_number}, Product Description: {product_description} - Error: Pack size not found\n")
                        error_count += 1
                        continue

                    # Check if the SKU already exists in the database
                    existing_sku = SKU.query.filter_by(material_number=material_number).first()
                    if existing_sku:
                        log_file.write(f"Material Number: {material_number} - Error: SKU already exists\n")
                        error_count += 1
                        continue

                    # Add new SKU with the matched pack_size_id and weight
                    new_sku = SKU(
                        material_number=material_number,
                        product_description=product_description,
                        pack_size_id=matched_pack_size,
                        weight=weight  # Include the weight
                    )
                    db.session.add(new_sku)
                    success_count += 1

            db.session.commit()
            flash(f'SKUs uploaded successfully! Total Success: {success_count}, Errors: {error_count}', 'success')

            if error_count > 0:
                flash(
                    f'Unsuccessful uploads were logged in {log_filename}. Please review and create missing pack sizes.',
                    'warning')

        except Exception as e:
            print(f"Error: {str(e)}")
            flash(f'Error processing file: {str(e)}', 'error')

        # Remove the file after processing
        os.remove(filepath)

        return redirect(url_for('list_skus_html'))

    return render_template('add_sku.html')

@app.route('/add-sku', methods=['GET', 'POST'])
@login_required
def add_sku_html():
    if request.method == 'POST':
        if 'material_number' in request.form:
            # Handle SKU form submission
            material_number = request.form['material_number']
            product_description = request.form['product_description']
            weight = float(request.form['weight'])  # Get the weight from the form

            # Fetch all pack sizes from the database
            pack_sizes = PackSize.query.all()

            # Try to automatically match the pack size from the product description
            matched_pack_size = None
            for pack_size in pack_sizes:
                if pack_size.size in product_description:
                    matched_pack_size = pack_size.id
                    break

            # If no match was found, show an error
            if not matched_pack_size:
                return render_template('add_sku.html', error="Pack size could not be detected from product description.", pack_sizes=pack_sizes)

            # Check if material number already exists
            existing_sku = SKU.query.filter_by(material_number=material_number).first()
            if existing_sku:
                return render_template('add_sku.html', error="Material Number already exists. Please use a different one.", pack_sizes=pack_sizes)

            # Add new SKU with the matched pack_size_id and weight
            new_sku = SKU(
                material_number=material_number,
                product_description=product_description,
                pack_size_id=matched_pack_size,
                weight=weight  # Store the weight
            )
            db.session.add(new_sku)
            db.session.commit()

            return redirect(url_for('list_skus_html'))

        elif 'size' in request.form:
            # Handle Pack Size form submission
            size = request.form['size']
            max_capacity = request.form['max_capacity']

            # Validate form data for Pack Size
            if not size or not max_capacity:
                return render_template('add_sku.html', error="Both pack size and max capacity are required.", pack_sizes=PackSize.query.all())

            # Check if the pack size already exists
            existing_pack_size = PackSize.query.filter_by(size=size).first()
            if existing_pack_size:
                return render_template('add_sku.html', error="Pack size already exists. Please use a different one.", pack_sizes=PackSize.query.all())

            # Create new pack size entry
            new_pack_size = PackSize(size=size, max_capacity=max_capacity)
            db.session.add(new_pack_size)
            db.session.commit()

            return redirect(url_for('add_sku_html'))

    # Fetch all pack sizes to display in the table
    pack_sizes = PackSize.query.all()

    return render_template('add_sku.html', pack_sizes=pack_sizes)

@app.route('/edit-sku/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_sku(id):
    sku = SKU.query.get_or_404(id)

    if request.method == 'POST':
        sku.material_number = request.form['material_number']
        sku.product_description = request.form['product_description']
        sku.weight = float(request.form['weight'])  # Allow editing the weight

        db.session.commit()
        return redirect(url_for('list_skus_html'))

    return render_template('edit_sku.html', sku=sku)

@app.route('/delete-sku/<int:id>', methods=['POST'])
@login_required
def delete_sku(id):
    # Fetch the SKU to be deleted
    sku = SKU.query.get_or_404(id)

    # Delete associated stock entries
    Stock.query.filter_by(sku_id=sku.id).delete()  # Delete all stock entries referencing this SKU

    # Now delete the SKU
    db.session.delete(sku)
    db.session.commit()
    return redirect(url_for('list_skus_html'))


@app.route('/sku-list', methods=['GET'])
@login_required
def list_skus_html():
    page = request.args.get('page', 1, type=int)  # Get the current page from the query parameters
    per_page = 10  # Set the number of SKUs per page
    search_term = request.args.get('search', '', type=str)  # Get the search term

    # Query SKUs based on the search term
    if search_term:
        skus = SKU.query.filter(
            SKU.material_number.ilike(f"%{search_term}%") |
            SKU.product_description.ilike(f"%{search_term}%")
        ).paginate(page=page, per_page=per_page, error_out=False)
    else:
        skus = SKU.query.paginate(page=page, per_page=per_page, error_out=False)

    # Calculate the range of pages to show in the pagination
    total_pages = skus.pages
    current_page = skus.page
    start_page = max(1, current_page - 2)
    end_page = min(total_pages, current_page + 2)

    return render_template(
        'sku_list.html',
        skus=skus.items,
        total_pages=total_pages,
        current_page=current_page,
        search_term=search_term,
        start_page=start_page,
        end_page=end_page
    )

@app.route('/create-stock', methods=['GET', 'POST'])
@login_required
def create_stock():
    if request.method == 'POST':
        if 'file' in request.files and request.files['file'].filename != '':
            file = request.files['file']

            try:
                # Read the Excel file
                df = pd.read_excel(file)

                for _, row in df.iterrows():
                    material_number = row['Material Number']
                    quantity = row['Quantity']
                    batch_number = row.get('Batch Number', '')
                    shipment_number = row.get('Shipment Number', '')
                    racking_number = row.get('Racking Number', '')
                    remarks = row.get('Remarks', '')

                    # Find the SKU by material number
                    sku = SKU.query.filter_by(material_number=material_number).first()

                    if sku:
                        # Ensure the pack size is valid and has a non-zero capacity
                        if not sku.pack_size or sku.pack_size.max_capacity <= 0:
                            return jsonify({'error': f'Pack size for SKU {material_number} is not valid or has zero capacity.'}), 400

                        # Get the max capacity from the pack size
                        max_capacity = sku.pack_size.max_capacity

                        # Calculate current quantity in the specified racking number
                        current_stock = Stock.query.filter_by(racking_number=racking_number).all()
                        total_quantity = sum(stock.quantity for stock in current_stock)

                        # If capacity is exceeded, return an error
                        if total_quantity + quantity > max_capacity:
                            return jsonify({'error': f'Racking {racking_number} exceeds its maximum capacity of {max_capacity}.'}), 400

                        # Add new stock entry
                        new_stock = Stock(
                            sku_id=sku.id,
                            quantity=quantity,
                            batch_number=batch_number,
                            shipment_number=shipment_number,
                            racking_number=racking_number,
                            remarks=remarks
                        )
                        db.session.add(new_stock)

                db.session.commit()  # Commit all stock entries to the database
                return jsonify({'message': 'Stocks added successfully!'}), 200

            except Exception as e:
                db.session.rollback()  # Rollback the session on error
                return jsonify({'error': str(e)}), 500

        else:
            return jsonify({'error': 'No file uploaded or file is empty!'}), 400

    racking_numbers = Racking.query.all()
    return render_template('create_stock.html', racking_numbers=racking_numbers)

@app.route('/manual-gr', methods=['GET', 'POST'])
@login_required
def manual_gr():
    if request.method == 'POST':
        if 'file' in request.files and request.files['file'].filename != '':
            file = request.files['file']

            try:
                # Read the Excel file
                df = pd.read_excel(file)

                for _, row in df.iterrows():
                    material_number = row['Material Number']
                    quantity = row['Quantity']
                    batch_number = row.get('Batch Number', '')
                    shipment_number = row.get('Shipment Number', '')
                    racking_number = row.get('Racking Number', '')
                    remarks = row.get('Remarks', '')

                    # Find the SKU by material number
                    sku = SKU.query.filter_by(material_number=material_number).first()

                    if sku:
                        # Ensure the pack size is valid and has a non-zero capacity
                        if not sku.pack_size or sku.pack_size.max_capacity <= 0:
                            return jsonify({'error': f'Pack size for SKU {material_number} is not valid or has zero capacity.'}), 400

                        # Get the max capacity from the pack size
                        max_capacity = sku.pack_size.max_capacity

                        # Calculate current quantity in the specified racking number
                        current_stock = Stock.query.filter_by(racking_number=racking_number).all()
                        total_quantity = sum(stock.quantity for stock in current_stock)

                        # If capacity is exceeded, return an error
                        if total_quantity + quantity > max_capacity:
                            return jsonify({'error': f'Racking {racking_number} exceeds its maximum capacity of {max_capacity}.'}), 400

                        # Add new stock entry
                        new_stock = Stock(
                            sku_id=sku.id,
                            quantity=quantity,
                            batch_number=batch_number,
                            shipment_number=shipment_number,
                            racking_number=racking_number,
                            remarks=remarks
                        )
                        db.session.add(new_stock)

                db.session.commit()  # Commit all stock entries to the database
                return jsonify({'message': 'Stocks added successfully!'}), 200

            except Exception as e:
                db.session.rollback()  # Rollback the session on error
                return jsonify({'error': str(e)}), 500

        else:
            return jsonify({'error': 'No file uploaded or file is empty!'}), 400

    racking_numbers = Racking.query.all()
    return render_template('manual_gr.html', racking_numbers=racking_numbers)

@app.route('/check-racking-capacity', methods=['POST'])
def check_racking_capacity():
    data = request.get_json() or {}
    racking_number   = (data.get('racking_number') or '').strip()
    material_number  = (data.get('material_number') or '').strip()
    incoming_quantity = int(data.get('incoming_quantity') or data.get('quantity') or 0)

    if not racking_number:
        return jsonify({'error': 'Racking number is required'}), 400

    # Bays that skip capacity checks (if you have these)
    special_bays = {"Disposal", "Damage", "Tinter", "Floor", "Chrome Room"}
    if racking_number in special_bays:
        return jsonify({
            'allowed': True,
            'current_quantity': 0,
            'max_capacity': None,
            'total_after': incoming_quantity
        }), 200

    racking = Racking.query.filter_by(racking_number=racking_number).first()
    if not racking:
        return jsonify({'error': 'Invalid racking number'}), 400

    # Sum current DB quantity in that rack
    current_items = Stock.query.filter_by(racking_number=racking_number).all()
    current_quantity = int(sum(int(s.quantity) for s in current_items))

    # Helper to get capacity from a SKU's pack size
    def capacity_for_material(mat_no: str):
        if not mat_no:
            return None
        sku = SKU.query.filter_by(material_number=mat_no).first()
        if not sku or not getattr(sku, 'pack_size', None):
            return None
        cap = getattr(sku.pack_size, 'max_capacity', None)
        try:
            return int(cap) if cap is not None else None
        except Exception:
            return None

    incoming_cap = capacity_for_material(material_number)

    # Decide max_capacity
    max_capacity = None
    warning_msg = None

    if current_items:
        if incoming_cap is not None:
            # Always use the incoming SKU's own pack size capacity
            max_capacity = incoming_cap
        else:
            # Incoming SKU has no pack size — fall back to existing items
            caps = []
            for s in current_items:
                sku = SKU.query.get(s.sku_id)
                if sku and getattr(sku, 'pack_size', None) and getattr(sku.pack_size, 'max_capacity', None) is not None:
                    try:
                        caps.append(int(sku.pack_size.max_capacity))
                    except Exception:
                        pass
            if caps:
                max_capacity = min(caps)
            else:
                warning_msg = f'Capacity check skipped for {racking_number}. No pack sizes configured.'
    else:
        # Rack is empty
        if incoming_cap is not None:
            max_capacity = incoming_cap
        else:
            # SKU has no pack size - allow but warn
            if material_number:
                sku_check = SKU.query.filter_by(material_number=material_number).first()
                if not sku_check:
                    warning_msg = f'Warning: Material {material_number} not found in SKU database. Capacity check skipped.'
                else:
                    warning_msg = f'Warning: Material {material_number} has no pack size configured. Capacity check skipped for {racking_number}.'
            else:
                warning_msg = f'Warning: No material number provided. Capacity check skipped for {racking_number}.'

    total_after = current_quantity + incoming_quantity

    # Only check capacity if we have a max_capacity value
    if max_capacity is not None:
        allowed = total_after <= max_capacity
    else:
        # No capacity limit configured - allow the transaction
        allowed = True

    payload = {
        'allowed': allowed,
        'current_quantity': current_quantity,
        'max_capacity': max_capacity,
        'total_after': total_after
    }

    # Add warning message if capacity check was skipped
    if warning_msg:
        payload['warning'] = warning_msg

    # Add warning if capacity exceeded
    if max_capacity is not None and not allowed:
        payload['warning'] = (f'Capacity exceeded for {racking_number}. '
                              f'Max: {max_capacity}, Current: {current_quantity}, Incoming: {incoming_quantity}.')

    return jsonify(payload), 200


# Helper function to calculate space units for a stock record
def calculate_space_units(stock):
    """Calculate total space units for a stock record"""
    sku = SKU.query.get(stock.sku_id)
    if not sku or not sku.pack_size:
        return stock.quantity * 1.0  # default 1.0 per item
    
    space_per_item = getattr(sku.pack_size, 'space_units', 1.0)
    return stock.quantity * space_per_item


@app.route('/get-pack-sizes', methods=['GET'])
def get_pack_sizes():
    try:
        # Query all pack sizes from the database
        pack_sizes = PackSize.query.all()

        # Format the data into a list of dictionaries for the frontend
        pack_size_data = [{'size': pack.size, 'max_capacity': pack.max_capacity} for pack in pack_sizes]

        # Return as JSON to the frontend
        return jsonify({'pack_sizes': pack_size_data}), 200
    except Exception as e:
        # Return error message in case something goes wrong
        return jsonify({'error': str(e)}), 500


# API route to search for SKU by material number (AJAX request)
@app.route('/search-sku', methods=['POST'])
@login_required
def search_sku():
    """Search SKU by material number OR product description"""
    try:
        data = request.get_json()
        search_term = (data.get('search_term') or '').strip()

        if not search_term:
            return jsonify({'results': []}), 200

        # Search by material_number OR product_description (case-insensitive)
        # Also filter for SKUs that have stock available
        skus = db.session.query(SKU).join(Stock).filter(
            (SKU.material_number.like(f'%{search_term}%')) |
            (SKU.product_description.like(f'%{search_term}%'))
        ).filter(Stock.quantity > 0).distinct().limit(10).all()

        results = []
        for sku in skus:
            results.append({
                'material_number': sku.material_number,
                'product_description': sku.product_description,
                'display': f"{sku.material_number} - {sku.product_description}"
            })

        return jsonify({'results': results}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Route to view all stock items
@app.route('/stocks', methods=['GET'])
@login_required
def list_stocks():
    # Get current page, search term, and "show zero quantity" option from query parameters
    page = request.args.get(get_page_parameter(), type=int, default=1)
    per_page = 10  # Number of items per page
    search_term = request.args.get('search', '').strip()
    show_zero_quantity = request.args.get('show_zero', 'false').lower() == 'true'

    # Base query: Fetch all stock entries, including those with quantity = 0
    query = Stock.query.join(SKU)

    # Apply the search filter if search_term exists
    if search_term:
        query = query.filter(
            or_(
                SKU.material_number.ilike(f"%{search_term}%"),
                SKU.product_description.ilike(f"%{search_term}%"),
                Stock.batch_number.ilike(f"%{search_term}%"),
                Stock.racking_number.ilike(f"%{search_term}%"),
                Stock.shipment_number.ilike(f"%{search_term}%"),
                Stock.remarks.ilike(f"%{search_term}%")
            )
        )

    # Filter out zero-quantity items if "show_zero_quantity" is False
    if not show_zero_quantity:
        query = query.filter(Stock.quantity > 0)

    # Get ALL matching stocks first (not paginated yet)
    all_stocks = query.all()

    # Calculate the total quantity for ALL filtered results
    total_quantity = sum(s.quantity for s in all_stocks)

    # Group ALL stocks first (before pagination)
    grouped = defaultdict(list)
    for stock in all_stocks:
        key = (
            stock.sku.material_number,
            stock.sku.product_description,
            stock.batch_number,
            stock.racking_number,
            stock.shipment_number,
            stock.remarks
        )
        grouped[key].append(stock)

    # Prepare the combined stock list
    combined_stocks = []
    for key, group in grouped.items():
        total_quantity_group = sum(s.quantity for s in group)
        first_id = group[0].id
        combined_stocks.append({
            'material_number': key[0],
            'product_description': key[1],
            'batch_number': key[2],
            'racking_number': key[3],
            'shipment_number': key[4],
            'remarks': key[5],
            'quantity': total_quantity_group,
            'id': first_id
        })

    # Now paginate the GROUPED results
    total_items = len(combined_stocks)
    start = (page - 1) * per_page
    end = start + per_page
    paginated_stocks = combined_stocks[start:end]

    # Create a simple pagination object with necessary properties
    class SimplePagination:
        def __init__(self, page, per_page, total):
            self.page = page
            self.per_page = per_page
            self.total = total
            self.pages = (total + per_page - 1) // per_page  # Ceiling division
            
        @property
        def has_prev(self):
            return self.page > 1
        
        @property
        def has_next(self):
            return self.page < self.pages
        
        @property
        def prev_num(self):
            return self.page - 1 if self.has_prev else None
        
        @property
        def next_num(self):
            return self.page + 1 if self.has_next else None
        
        def iter_pages(self, left_edge=2, left_current=2, right_current=3, right_edge=2):
            """Generator for page numbers to display in pagination"""
            last = 0
            for num in range(1, self.pages + 1):
                if (num <= left_edge or 
                    (num > self.page - left_current - 1 and num < self.page + right_current) or
                    num > self.pages - right_edge):
                    if last + 1 != num:
                        yield None  # Gap in page numbers
                    yield num
                    last = num

    # Create pagination object
    pagination = SimplePagination(page=page, per_page=per_page, total=total_items)

    return render_template(
        'stock_list.html',
        stocks=paginated_stocks,
        pagination=pagination,
        search_term=search_term,
        total_quantity=total_quantity,
        show_zero_quantity=show_zero_quantity
    )

@app.route('/stock-history/<int:stock_id>', methods=['GET'])
@login_required
def stock_history(stock_id):
    # Fetch the stock item based on the stock_id
    stock = Stock.query.get_or_404(stock_id)

    # Fetch all history records for this stock item
    history = StockHistory.query.filter_by(stock_id=stock_id).all()

    return render_template('stock_history.html', stock=stock, history=history)


# Route for adjusting stock (GR and GI)
@app.route('/adjust-stock/<int:stock_id>', methods=['POST'])
@login_required
def adjust_stock(stock_id):
    stock = Stock.query.get_or_404(stock_id)
    adjustment = int(request.form['adjustment'])
    action = request.form['action']

    if adjustment <= 0:
        return jsonify({'error': 'Adjustment quantity must be greater than 0'}), 400

    if action == 'gr':  # Goods Receipt
        stock.quantity += adjustment
        change_type = "GR"
    elif action == 'gi':  # Goods Issue
        if adjustment > stock.quantity:
            return jsonify({'error': 'Not enough stock to issue this quantity'}), 400
        stock.quantity -= adjustment
        change_type = "GI"

    # Log the stock change in the StockHistory table
    new_history = StockHistory(
        stock_id=stock.id,
        change_type=change_type,
        quantity=adjustment,
        username=current_user.username
    )
    db.session.add(new_history)

    db.session.commit()

    return redirect(url_for('list_stocks'))


@app.route('/daily-rack-count')
@login_required
def daily_rack_count():
    """Display daily rack count data with utilization % and estimated volume"""
    from datetime import timedelta
    import pytz
    import re
 
    selected_month_str = request.args.get('month', default=None)
 
    if selected_month_str:
        try:
            selected_month = datetime.strptime(selected_month_str, '%Y-%m')
        except ValueError:
            selected_month = datetime.now()
    else:
        selected_month = datetime.now()
 
    days_in_month = monthrange(selected_month.year, selected_month.month)[1]
    today = datetime.now(pytz.timezone('Asia/Kuala_Lumpur')).date()
 
    # Get all daily rack count records for the selected month
    daily_rack_counts = (
        db.session.query(DailyRackCount)
        .filter(extract('month', DailyRackCount.date) == selected_month.month)
        .filter(extract('year', DailyRackCount.date) == selected_month.year)
        .order_by(DailyRackCount.date.asc())
        .all()
    )
 
    existing_data = {}
    for record in daily_rack_counts:
        existing_data[record.date] = {
            'date': record.date,
            'occupied_racks': record.occupied_racks,
            'is_auto_generated': False
        }
 
    # Build complete list up to today only
    complete_records = []
    first_day = datetime(selected_month.year, selected_month.month, 1).date()
    last_day  = datetime(selected_month.year, selected_month.month, days_in_month).date()
    if last_day > today:
        last_day = today
 
    current_date   = first_day
    previous_value = None
 
    while current_date <= last_day:
        if current_date in existing_data:
            complete_records.append(existing_data[current_date])
            previous_value = existing_data[current_date]['occupied_racks']
        else:
            if previous_value is not None:
                complete_records.append({
                    'date': current_date,
                    'occupied_racks': previous_value,
                    'is_auto_generated': True
                })
        current_date += timedelta(days=1)
 
    # Total racks for utilization %
    total_racks = Racking.query.count()
 
    # ── Estimated Volume from CURRENT stock ───────────────────────────────
    # pack_size.size is a string like "20L", "5L", "1.43L", "20lt"
    # We extract the numeric part and multiply by stock quantity
    all_stocks = Stock.query.filter(Stock.quantity > 0).all()
 
    total_volume_litres = 0.0
    for stock in all_stocks:
        try:
            if stock.sku and stock.sku.pack_size and stock.sku.pack_size.size:
                size_str = str(stock.sku.pack_size.size)
                # Extract first number from e.g. "20L", "5L", "1.43L", "20lt", "20"
                match = re.search(r'\d+\.?\d*', size_str)
                if match:
                    litres = float(match.group())
                    total_volume_litres += stock.quantity * litres
        except Exception:
            pass
 
    total_volume_litres = round(total_volume_litres, 2)
 
    # ── Statistics ────────────────────────────────────────────────────────
    if complete_records:
        max_occupied    = max(r['occupied_racks'] for r in complete_records)
        min_occupied    = min(r['occupied_racks'] for r in complete_records)
        total_records   = len(complete_records)
        manual_records  = sum(1 for r in complete_records if not r['is_auto_generated'])
        auto_records    = sum(1 for r in complete_records if r['is_auto_generated'])
        total_occupied  = sum(r['occupied_racks'] for r in complete_records)
        monthly_average = total_occupied / days_in_month if days_in_month else 0
 
        # Average utilization % across all recorded days
        if total_racks > 0:
            avg_utilization = round(
                sum((r['occupied_racks'] / total_racks) * 100 for r in complete_records)
                / len(complete_records), 1
            )
        else:
            avg_utilization = 0.0
    else:
        max_occupied = min_occupied = total_records = manual_records = 0
        auto_records = total_occupied = 0
        monthly_average = avg_utilization = 0.0
 
    return render_template(
        'daily_rack_count.html',
        daily_rack_counts=complete_records,
        selected_month=selected_month_str or selected_month.strftime('%Y-%m'),
        max_occupied=max_occupied,
        min_occupied=min_occupied,
        total_daily_records=total_records,
        manual_records=manual_records,
        auto_records=auto_records,
        days_in_month=days_in_month,
        monthly_average=monthly_average,
        total_occupied=total_occupied,
        total_racks=total_racks,
        avg_utilization=avg_utilization,
        total_volume_litres=total_volume_litres,
    )

# REPLACE your existing daily_orders route in app.py with this:

@app.route('/daily-orders')
@login_required
def daily_orders():
    """Display daily order creation count with weight summary"""

    selected_month_str = request.args.get('month', default=None)

    if selected_month_str:
        try:
            selected_month = datetime.strptime(selected_month_str, '%Y-%m')
        except ValueError:
            selected_month = datetime.now()
    else:
        selected_month = datetime.now()

    days_in_month = monthrange(selected_month.year, selected_month.month)[1]

    orders = (
        db.session.query(Order)
        .filter(extract('month', Order.created_at) == selected_month.month)
        .filter(extract('year', Order.created_at) == selected_month.year)
        .filter(Order.status != 'cancelled')
        .all()
    )

    from collections import defaultdict

    # Build daily_data: date -> { count, weight }
    daily_data = defaultdict(lambda: {'count': 0, 'weight': 0.0})

    for order in orders:
        if order.dn_number and order.dn_number.strip().isdigit():
            order_date = order.created_at.date()
            daily_data[order_date]['count'] += 1

            # Sum weight from all order items
            for item in order.items:
                try:
                    daily_data[order_date]['weight'] += item.quantity * item.sku.weight
                except Exception:
                    pass

    # Sort into list of (date, count, weight)
    daily_order_data = sorted(
        [(date, v['count'], round(v['weight'], 2)) for date, v in daily_data.items()],
        key=lambda x: x[0]
    )

    if daily_order_data:
        total_orders           = sum(r[1] for r in daily_order_data)
        total_weight           = round(sum(r[2] for r in daily_order_data), 2)
        max_orders             = max(r[1] for r in daily_order_data)
        min_orders             = min(r[1] for r in daily_order_data)
        total_days_with_orders = len(daily_order_data)
        daily_average          = total_orders / days_in_month if days_in_month else 0
    else:
        total_orders = max_orders = min_orders = total_days_with_orders = 0
        total_weight  = 0.0
        daily_average = 0

    return render_template(
        'daily_orders.html',
        daily_order_data=daily_order_data,
        selected_month=selected_month_str or selected_month.strftime('%Y-%m'),
        total_orders=total_orders,
        total_weight=total_weight,
        max_orders=max_orders,
        min_orders=min_orders,
        total_days_with_orders=total_days_with_orders,
        days_in_month=days_in_month,
        daily_average=daily_average
    )


@app.route('/dashboard', methods=['GET'])
@login_required
def dashboard():
    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Number of items per page

    # Total SKUs
    total_skus = SKU.query.count()

    # Total Stock Quantity
    total_stock_quantity = db.session.query(func.sum(Stock.quantity)).scalar() or 0

    # SKUs with Zero Stock
    zero_stock_skus = (
        db.session.query(SKU.material_number)
        .outerjoin(Stock, SKU.id == Stock.sku_id)  # Join SKU with Stock
        .group_by(SKU.material_number)  # Group by material number
        .having(func.sum(Stock.quantity) == 0)  # Only count if total stock quantity is 0
        .count()
    )

    # Low Stock Alerts with pagination
    low_stock_threshold = request.args.get('threshold', default=10, type=int)
    low_stock_items = (
        db.session.query(
            SKU.material_number,
            SKU.product_description,
            Stock.batch_number,
            func.sum(Stock.quantity).label('total_quantity')
        )
        .join(SKU, SKU.id == Stock.sku_id)
        .group_by(SKU.material_number, SKU.product_description, Stock.batch_number)
        .having(func.sum(Stock.quantity) <= low_stock_threshold)
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    # Top 5 SKUs by Quantity
    top_skus = (
        db.session.query(
            SKU.material_number,
            SKU.product_description,
            func.sum(Stock.quantity).label('total_quantity')
        )
        .join(SKU, SKU.id == Stock.sku_id)
        .group_by(SKU.material_number, SKU.product_description)
        .order_by(func.sum(Stock.quantity).desc())
        .limit(5)
        .all()
    )

    # Recent Stock Adjustments
    recent_adjustments = StockHistory.query.order_by(StockHistory.timestamp.desc()).limit(5).all()

    # Total racks
    total_racks = Racking.query.count()

    # Occupied racks (racking with at least one stock entry)
    occupied_racks = db.session.query(Stock.racking_number) \
        .filter(Stock.quantity > 0) \
        .distinct() \
        .count()

    # Calculate utilization per rack
    stock_with_capacity = (
        db.session.query(
            Stock.racking_number,
            func.sum(Stock.quantity).label('current_quantity'),
            PackSize.max_capacity
        )
        .join(SKU, SKU.id == Stock.sku_id)
        .join(PackSize, PackSize.id == SKU.pack_size_id)
        .group_by(Stock.racking_number, PackSize.max_capacity)
        .all()
    )

    utilization_percentages = []
    for stock in stock_with_capacity:
        if stock.max_capacity > 0:
            utilization_percentage = (stock.current_quantity / stock.max_capacity) * 100
            utilization_percentages.append(utilization_percentage)

    average_utilization_percentage = sum(utilization_percentages) / len(utilization_percentages) if utilization_percentages else 0

    # Handle month selection for Pallets and Orders
    selected_month_pallets_str = request.args.get('month_pallets', default=None)
    selected_month_orders_str = request.args.get('month_orders', default=None)

    # Default to current month if no month is selected
    if selected_month_pallets_str:
        try:
            selected_month_pallets = datetime.strptime(selected_month_pallets_str, '%Y-%m')
        except ValueError:
            selected_month_pallets = datetime.now()
    else:
        selected_month_pallets = datetime.now()

    if selected_month_orders_str:
        try:
            selected_month_orders = datetime.strptime(selected_month_orders_str, '%Y-%m')
        except ValueError:
            selected_month_orders = datetime.now()
    else:
        selected_month_orders = datetime.now()

    # =====================================================================
    # FIXED: Monthly Average Occupied Pallets with Auto-Fill
    # Now matches the daily_rack_count page calculation!
    # =====================================================================
    from datetime import timedelta
    import pytz
    
    days_in_month = monthrange(selected_month_pallets.year, selected_month_pallets.month)[1]
    
    # Get today's date
    today = datetime.now(pytz.timezone('Asia/Kuala_Lumpur')).date()
    
    # Get all daily rack count records for the selected month
    daily_rack_counts_query = (
        db.session.query(DailyRackCount)
        .filter(extract('month', DailyRackCount.date) == selected_month_pallets.month)
        .filter(extract('year', DailyRackCount.date) == selected_month_pallets.year)
        .order_by(DailyRackCount.date.asc())
        .all()
    )
    
    # Convert to dictionary for easy lookup
    existing_data = {}
    for record in daily_rack_counts_query:
        existing_data[record.date] = record.occupied_racks
    
    # Create complete list for the month (UP TO TODAY ONLY)
    complete_records = []
    first_day = datetime(selected_month_pallets.year, selected_month_pallets.month, 1).date()
    last_day = datetime(selected_month_pallets.year, selected_month_pallets.month, days_in_month).date()
    
    # Don't go beyond today's date
    if last_day > today:
        last_day = today
    
    current_date = first_day
    previous_value = None
    
    while current_date <= last_day:
        if current_date in existing_data:
            # Day exists - use actual data
            complete_records.append(existing_data[current_date])
            previous_value = existing_data[current_date]
        else:
            # Day missing - auto-fill from previous day
            if previous_value is not None:
                complete_records.append(previous_value)
            # If no previous value, skip (can't auto-fill first day)
        
        current_date += timedelta(days=1)
    
    # Calculate monthly average from complete data (manual + auto-filled)
    if complete_records:
        monthly_total_occupied = sum(complete_records)
        monthly_average_occupied_pallets = monthly_total_occupied / days_in_month
    else:
        monthly_total_occupied = 0
        monthly_average_occupied_pallets = 0
    # =====================================================================
    # END OF FIX - Now matches daily_rack_count page!
    # =====================================================================

    # Monthly GI Total Order Items
    monthly_total_order_items = (
        db.session.query(func.sum(OrderItem.quantity))
        .join(Order)
        .filter(extract('month', Order.created_at) == selected_month_orders.month)
        .filter(extract('year', Order.created_at) == selected_month_orders.year)
        .filter(Order.status != 'cancelled')
        .scalar() or 0
    )

    # Monthly GI Total Order Weight
    monthly_order_weight = (
        db.session.query(func.sum(OrderItem.quantity * SKU.weight))
        .join(Order)
        .join(SKU, SKU.id == OrderItem.sku_id)
        .filter(extract('month', Order.created_at) == selected_month_orders.month)
        .filter(extract('year', Order.created_at) == selected_month_orders.year)
        .filter(Order.status != 'cancelled')
        .scalar() or 0
    )

    return render_template(
        'dashboard.html',
        total_skus=total_skus,
        total_stock_quantity=total_stock_quantity,
        zero_stock_skus=zero_stock_skus,
        low_stock_items=low_stock_items,
        low_stock_threshold=low_stock_threshold,
        top_skus=top_skus,
        recent_adjustments=recent_adjustments,
        total_racks=total_racks,
        occupied_racks=occupied_racks,
        empty_racks=total_racks - occupied_racks,
        utilization_percentage=average_utilization_percentage,
        monthly_average_occupied_pallets=monthly_average_occupied_pallets,
        monthly_total_order_items=monthly_total_order_items,
        monthly_order_weight=monthly_order_weight,
        selected_month_pallets=selected_month_pallets_str,
        selected_month_orders=selected_month_orders_str
    )

def _parse_position_to_col_bin(position) -> tuple[str, int]:
    """
    Accepts 'A1', 'B2', 'C1', etc. Returns (column_letter, bin_index).
    Falls back to ('A', 1) if not parseable.
    """
    s = str(position).strip().upper()
    m = re.match(r'^([A-Z]+)\s*-?\s*(\d+)$', s)
    if m:
        col = m.group(1)
        try:
            bin_idx = int(m.group(2))
        except ValueError:
            bin_idx = 1
        return col, bin_idx
    # If they passed just a digit like "1" or 1
    if s.isdigit():
        return 'A', int(s)
    return 'A', 1

def generate_racking_number(aisle: str, bay_no: int, column_letter: str, bin_index: int) -> str:
    """
    New format:
      <Aisle>-<Bay 2 digits>-<ColumnLetter><BinIndex>
    Examples:
      A-01-A1, A-01-A2, ... A-01-E1, A-01-E2
    """
    return f"{aisle.upper()}-{int(bay_no):02d}-{column_letter.upper()}{int(bin_index)}"


def generate_racking_data(
    aisles,
    positions=None,          # kept for backward-compat (ignored)
    levels=None,             # your 'levels' are treated as bay numbers
    *,
    columns=("A", "B", "C", "D", "E"),
    bins=(1, 2)
):
    """
    Backward-compatible generator for racking rows.

    - 'levels' (existing param): list/iterable of bay numbers (e.g., [1,2,3,...])
    - 'positions' (existing param): IGNORED now (we always build A–E × {1,2})
    - 'columns' (kw-only, optional): iterable of column letters, default A–E
    - 'bins' (kw-only, optional): iterable of bin indices per column, default 1,2

    Returns a list of dicts with:
      racking_number, aisle, level, position, location
    Where:
      - level   = bay number (int)
      - position= "<ColumnLetter><BinIndex>" (e.g., "A1", "B2")
      - location= "BAY-<BayNo 2d>/<ColumnLetter>" (cosmetic)
    """
    if not aisles:
        return []

    # If levels not provided or empty, default to bay "01"
    bay_numbers = list(levels) if levels else [1]

    racking_list = []
    for aisle in aisles:
        for bay_no in bay_numbers:
            for col in columns:
                for b in bins:
                    racking_number = generate_racking_number(aisle, bay_no, col, b)
                    racking_list.append({
                        "racking_number": racking_number,
                        "aisle": str(aisle).upper(),
                        "level": int(bay_no),               # keep DB column semantics
                        "position": f"{col.upper()}{int(b)}",# store like "A1", "B2"
                        "location": f"BAY-{int(bay_no):02d}/{col.upper()}",
                    })
    return racking_list


@app.route('/add', methods=['POST'])
def add_racking():
    # Get values from the form
    aisle_input = request.form.get('aisle')
    level_input = request.form.get('level')
    position_input = request.form.get('position')

    # Validate form inputs
    if not aisle_input or not level_input or not position_input:
        flash('All fields are required.', 'danger')
        return redirect(url_for('create_racking'))

    try:
        levels_int = int(level_input)
        positions_int = int(position_input)
    except ValueError:
        flash('Levels and Positions must be integers.', 'danger')
        return redirect(url_for('create_racking'))

    # Process aisles: split by comma, strip whitespace, and convert to uppercase
    aisles = [a.strip().upper() for a in aisle_input.split(',') if a.strip()]
    if not aisles:
        flash('At least one aisle must be specified.', 'danger')
        return redirect(url_for('create_racking'))

    # Generate lists for levels and positions
    levels = list(range(1, levels_int + 1))        # Levels 1 to levels_int
    positions = list(range(1, positions_int + 1))  # Positions 1 to positions_int

    # Generate racking data based on the inputs
    generated_data = generate_racking_data(aisles, positions, levels)

    # Counter for new racks added
    new_racks_added = 0

    with app.app_context():
        for rack in generated_data:
            # Generate racking number
            col_letter, bin_index = _parse_position_to_col_bin(rack['position'])
            racking_number = generate_racking_number(rack['aisle'], rack['level'], col_letter, bin_index)

            # Check if the racking number already exists in the database
            existing_racking = Racking.query.filter_by(racking_number=racking_number).first()

            if existing_racking:
                # Racking number exists; skip insertion
                continue
            else:
                # Create a new Racking instance
                new_rack = Racking(
                    racking_number=racking_number,
                    aisle=rack['aisle'],
                    level=rack['level'],
                    position=rack['position'],
                    location=rack['location']
                )
                db.session.add(new_rack)
                new_racks_added += 1

        try:
            # Commit all new racks to the database
            db.session.commit()
            if new_racks_added == 0:
                flash('No new racks added. All racking numbers already exist.', 'info')
            else:
                flash(f'Added {new_racks_added} new rack(s).', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding racks: {str(e)}', 'danger')

    return redirect(url_for('create_racking'))

@app.route('/create_racking', methods=['GET'])
def create_racking():
    # Get the 'aisle' query parameter if present
    selected_aisle = request.args.get('aisle')

    with app.app_context():
        if selected_aisle:
            # Fetch racks for the selected aisle
            racking_entries = Racking.query.filter_by(aisle=selected_aisle.upper()).all()
        else:
            # Fetch all racks
            racking_entries = Racking.query.all()

        # Fetch all unique aisles for button generation
        unique_aisles = db.session.query(Racking.aisle).distinct().all()
        # Convert list of tuples to a flat list
        unique_aisles = [aisle[0] for aisle in unique_aisles]

    return render_template('create_racking.html', racking_data=racking_entries, unique_aisles=unique_aisles, selected_aisle=selected_aisle)

@app.route('/delete/<racking_number>')
def delete_racking(racking_number):
    with app.app_context():
        # Query the racking by racking_number
        racking = Racking.query.filter_by(racking_number=racking_number).first()
        if racking:
            db.session.delete(racking)
            try:
                db.session.commit()
                flash(f'Racking {racking_number} deleted successfully.', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error deleting racking {racking_number}: {str(e)}', 'danger')
        else:
            flash(f'Racking {racking_number} not found.', 'danger')
    return redirect(url_for('create_racking'))

# Use this function to insert data into the database
def insert_racking_data(aisles, positions, levels):
    # Generate racking data based on user input or defaults
    racking_data = generate_racking_data(aisles, positions, levels)

    # Ensure the Flask app context is active
    with app.app_context():
        for racking in racking_data:
            # Check if the racking number already exists in the database
            existing_racking = Racking.query.filter_by(racking_number=racking['racking_number']).first()

            if existing_racking:
                # If the racking number already exists, skip it
                print(f"Racking {racking['racking_number']} already exists, skipping.")
            else:
                # Insert the racking if it doesn't exist
                new_racking = Racking(racking_number=racking['racking_number'], aisle=racking['aisle'])
                db.session.add(new_racking)

        try:
            db.session.commit()  # Commit all the new rackings to the database
            print("Racking data inserted successfully!")
        except Exception as e:
            db.session.rollback()  # Rollback if there's any error
            print(f"Error inserting racking data: {e}")


@app.route('/transfer-stock', methods=['GET'])
@login_required
def show_transfer_stock():
    material_number_search = request.args.get('material_number', '').strip()
    batch_number_search = request.args.get('batch_number', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 100  # CHANGED: Was 10, now 100 (shows more items)

    query = Stock.query.join(SKU)

    if material_number_search:
        query = query.filter(SKU.material_number.ilike(f"%{material_number_search}%"))

    if batch_number_search:
        query = query.filter(Stock.batch_number.ilike(f"%{batch_number_search}%"))

    # ADDED: Filter out zero quantity stocks (like stock list does)
    query = query.filter(Stock.quantity > 0)

    stocks_paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        response_data = [{
            'id': stock.id,
            'material_number': stock.sku.material_number,
            'product_description': stock.sku.product_description,
            'batch_number': stock.batch_number,
            'quantity': stock.quantity,
            'racking_number': stock.racking_number
        } for stock in stocks_paginated.items]
        return jsonify({
            'stocks': response_data,
            'has_next': stocks_paginated.has_next,
            'has_prev': stocks_paginated.has_prev,
            'page': stocks_paginated.page,
            'total_pages': stocks_paginated.pages
        })

    racking_numbers = Racking.query.all()
    return render_template('transfer_stock.html', stocks=stocks_paginated.items, racking_numbers=racking_numbers)

@app.route('/transfer-stock/<int:stock_id>', methods=['POST'])
@login_required
def transfer_stock(stock_id):
    try:
        data = request.get_json()
        quantity_to_transfer = data.get('quantity')
        new_racking_number = data.get('new_racking_number')

        # Fetch the stock to be updated
        stock = Stock.query.get(stock_id)
        if not stock:
            return jsonify({'error': 'Stock not found'}), 404

        if quantity_to_transfer > stock.quantity:
            return jsonify({'error': 'Insufficient stock quantity for transfer'}), 400

        # Check if the new racking number is valid
        racking = Racking.query.filter_by(racking_number=new_racking_number).first()
        if not racking:
            return jsonify({'error': 'Invalid racking number'}), 400

        # Calculate the total quantity in the target racking for all SKUs
        total_quantity_in_new_racking = db.session.query(db.func.sum(Stock.quantity)).filter_by(racking_number=new_racking_number).scalar() or 0

        # Get the max capacity based on the SKU's pack size
        pack_size = stock.sku.pack_size
        max_capacity = pack_size.max_capacity

        # Calculate the final quantity after transfer
        final_quantity = total_quantity_in_new_racking + quantity_to_transfer

        if final_quantity > max_capacity:
            return jsonify({'error': f'Exceeds new racking capacity of {max_capacity}. Current quantity: {total_quantity_in_new_racking}, Adding: {quantity_to_transfer}'}), 400

        # Reduce the quantity in the current stock
        stock.quantity -= quantity_to_transfer

        # Create a new stock entry for the transferred quantity in the new racking
        new_stock = Stock(
            sku_id=stock.sku_id,
            quantity=quantity_to_transfer,
            batch_number=stock.batch_number,
            shipment_number=stock.shipment_number,
            racking_number=new_racking_number,
            remarks=stock.remarks
        )
        db.session.add(new_stock)
        db.session.flush()  # Flush to get the new_stock ID for history entry

        # Log the transfer in stock history
        stock_history_outgoing = StockHistory(
            stock_id=stock.id,
            change_type=f'Transferred {quantity_to_transfer} to racking {new_racking_number}',
            quantity=-quantity_to_transfer,
            username=current_user.username
        )
        db.session.add(stock_history_outgoing)

        stock_history_incoming = StockHistory(
            stock_id=new_stock.id,
            change_type=f'Received {quantity_to_transfer} from racking {stock.racking_number}',
            quantity=quantity_to_transfer,
            username=current_user.username
        )
        db.session.add(stock_history_incoming)

        db.session.commit()
        return jsonify({'success': True}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Error transferring stock: {str(e)}'}), 500


@app.route('/transfer-batch', methods=['GET'])
@login_required
def show_transfer_batch():
    # Get search parameters
    material_number_search = request.args.get('material_number', '').strip()
    batch_number_search = request.args.get('batch_number', '').strip()
    page = request.args.get(get_page_parameter(), type=int, default=1)
    per_page = 100  # CHANGED: Was 10, now 100 (shows more items)

    # Base query to fetch all stocks and join with SKU
    query = Stock.query.join(SKU)

    # Filter by material number if provided
    if material_number_search:
        query = query.filter(SKU.material_number.ilike(f"%{material_number_search}%"))

    # Filter by batch number if provided
    if batch_number_search:
        query = query.filter(Stock.batch_number.ilike(f"%{batch_number_search}%"))

    # ADDED: Filter out zero quantity stocks (like stock list does)
    query = query.filter(Stock.quantity > 0)

    # Paginate the query
    paginated_query = query.paginate(page=page, per_page=per_page, error_out=False)
    stocks = paginated_query.items

    # Assume `batch_numbers` is a list of all batch numbers available in the system
    batch_numbers = db.session.query(Stock.batch_number).distinct().all()
    batch_numbers = [batch[0] for batch in batch_numbers]  # Extract only batch numbers

    # Render the template with paginated stocks and batch numbers for selection
    return render_template(
        'transfer_batch.html',  # Make sure this matches the template name
        stocks=stocks,
        batch_numbers=batch_numbers,
        pagination=Pagination(page=page, per_page=per_page, total=paginated_query.total, css_framework='bootstrap5'),
        material_number_search=material_number_search,
        batch_number_search=batch_number_search
    )


@app.route('/transfer-batch/<int:stock_id>', methods=['POST'])
@login_required
def transfer_batch(stock_id):
    try:
        data = request.get_json() or {}

        # Validate inputs
        raw_qty = data.get('quantity', None)
        new_batch_number = (data.get('new_batch_number') or '').strip()

        if raw_qty is None:
            return jsonify({'error': 'Quantity is required'}), 400
        try:
            # accept ints or decimals; store as numeric
            quantity_to_transfer = float(raw_qty)
        except Exception:
            return jsonify({'error': 'Quantity must be a number'}), 400

        if quantity_to_transfer <= 0:
            return jsonify({'error': 'Quantity must be greater than 0'}), 400

        if not new_batch_number:
            return jsonify({'error': 'New batch number is required'}), 400

        # Load source stock
        stock = Stock.query.get(stock_id)
        if not stock:
            return jsonify({'error': 'Stock not found'}), 404

        if new_batch_number == (stock.batch_number or ''):
            return jsonify({'error': 'New batch number is the same as current'}), 400

        if quantity_to_transfer > float(stock.quantity):
            return jsonify({'error': 'Insufficient stock quantity for transfer'}), 400

        # ========= IMPORTANT CHANGE =========
        # This is a relabel-only operation (same racking). Do NOT call the
        # capacity check; no new physical quantity is entering the rack.
        # ===================================

        # Reduce quantity at source
        stock.quantity = float(stock.quantity) - quantity_to_transfer

        # Merge only within the SAME rack + same shipment/remarks
        existing_stock = Stock.query.filter_by(
            sku_id=stock.sku_id,
            batch_number=new_batch_number,
            racking_number=stock.racking_number,
            shipment_number=stock.shipment_number,
            remarks=stock.remarks
        ).first()

        if existing_stock:
            existing_stock.quantity = float(existing_stock.quantity) + quantity_to_transfer
        else:
            existing_stock = Stock(
                sku_id=stock.sku_id,
                quantity=quantity_to_transfer,
                batch_number=new_batch_number,
                shipment_number=stock.shipment_number,
                racking_number=stock.racking_number,
                remarks=stock.remarks
            )
            db.session.add(existing_stock)

        db.session.commit()

        # History logs
        new_stock_history = StockHistory(
            stock_id=existing_stock.id,
            change_type=f'Batch transfer received from {stock.batch_number}',
            quantity=quantity_to_transfer,
            username=current_user.username
        )
        stock_history_outgoing = StockHistory(
            stock_id=stock.id,
            change_type=f'Batch transfer out to {new_batch_number}',
            quantity=-quantity_to_transfer,
            username=current_user.username
        )
        db.session.add(new_stock_history)
        db.session.add(stock_history_outgoing)

        db.session.commit()
        return jsonify({'success': True}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Exception occurred: {e}")
        return jsonify({'error': f'Error transferring stock: {str(e)}'}), 500


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            # Automatically approve admin users during login
            if user.is_admin:
                user.is_approved = True
                db.session.commit()

            # Check if the user is approved (non-admins need approval)
            if not user.is_approved:
                flash('Your account is awaiting approval by the admin.', 'warning')
                return redirect(url_for('login'))

            login_user(user)
            flash('Login successful!', 'success')

            # Check and record today's occupied rack count
            today = date.today()
            existing_entry = DailyRackCount.query.filter_by(date=today).first()

            if not existing_entry:
                # Query for the current occupied racks count
                occupied_racks = db.session.query(Stock.racking_number) \
                    .filter(Stock.quantity > 0) \
                    .distinct() \
                    .count()

                # Insert a new record for today's occupied rack count
                new_entry = DailyRackCount(date=today, occupied_racks=occupied_racks)
                db.session.add(new_entry)
                db.session.commit()

            # Redirect admin users to a separate dashboard if needed
            if user.is_admin:
                return redirect(url_for('index'))
            return redirect(url_for('index'))

        else:
            flash('Invalid username or password', 'danger')

    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']

        # Check if the email already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('This email is already registered. Please use a different email.', 'danger')
            return redirect(url_for('register'))

        # Hash the password
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        new_user = User(username=username, email=email, password=hashed_password)

        db.session.add(new_user)
        db.session.commit()

        flash('Registration successful! You can now log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        user = User.query.filter_by(username=username).first()

        if user:
            # Redirect to reset password page
            return redirect(url_for('reset_password', username=username))
        else:
            flash('Invalid username. Please try again.', 'danger')
            return render_template('forgot_password.html')

    return render_template('forgot_password.html')


# Reset Password Route
@app.route('/reset-password/<username>', methods=['GET', 'POST'])
def reset_password(username):
    user = User.query.filter_by(username=username).first()

    if not user:
        flash('Invalid request.', 'danger')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        new_password = request.form.get('new_password').strip()
        confirm_password = request.form.get('confirm_password').strip()

        if new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('reset_password.html', username=username)

        # Hash the new password using pbkdf2:sha256 and update the user's record
        user.password = generate_password_hash(new_password, method='pbkdf2:sha256')
        db.session.commit()

        flash('Password updated successfully. Please login with your new password.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', username=username)


@app.route('/get-sku-info', methods=['POST'])
@login_required
def get_sku_info():
    data = request.get_json()
    material_number = data.get('material_number')

    # UPDATED: Search by material_number OR product_description
    sku = SKU.query.filter(
        (SKU.material_number == material_number) |
        (SKU.product_description == material_number)
    ).first()

    # If not found by exact match, try partial match
    if not sku:
        sku = SKU.query.filter(
            (SKU.material_number.like(f'%{material_number}%')) |
            (SKU.product_description.like(f'%{material_number}%'))
        ).first()

    if not sku:
        return jsonify({'error': 'SKU not found'}), 404

    # Get stock entries for the SKU
    stock_entries = Stock.query.filter_by(sku_id=sku.id).filter(Stock.quantity > 0).all()

    if not stock_entries:
        return jsonify({'error': 'No stock available for this SKU'}), 404

    # Prepare data structures
    available_quantities = {}
    batch_numbers = set()
    shipment_numbers = set()

    for stock in stock_entries:
        batch = stock.batch_number
        racking = stock.racking_number
        shipment = stock.shipment_number or "N/A"

        batch_numbers.add(batch)
        shipment_numbers.add(shipment)

        if batch not in available_quantities:
            available_quantities[batch] = {}
        if racking not in available_quantities[batch]:
            available_quantities[batch][racking] = {}
        if shipment not in available_quantities[batch][racking]:
            available_quantities[batch][racking][shipment] = 0
        available_quantities[batch][racking][shipment] += stock.quantity

    # Convert sets to lists
    batch_numbers = list(batch_numbers)
    racking_numbers = list({r for batch in available_quantities.values() for r in batch.keys()})
    shipment_numbers = list(shipment_numbers)

    # Return data - INCLUDE material_number in response
    return jsonify({
        'material_number': sku.material_number,  # ← ADDED
        'product_description': sku.product_description,
        'batch_numbers': batch_numbers,
        'racking_numbers': racking_numbers,
        'shipment_numbers': shipment_numbers,
        'available_quantities': available_quantities,
        'weight': sku.weight
    }), 200

@app.route('/check-quantity', methods=['POST'])
def check_quantity():
    data = request.get_json()
    material_number = data.get('material_number')
    batch_number = data.get('batch_number')

    sku = SKU.query.filter_by(material_number=material_number).first()

    if not sku:
        return jsonify({'error': 'SKU not found'}), 404

    # Calculate the total quantity available for the given batch
    stock = Stock.query.filter_by(sku_id=sku.id, batch_number=batch_number).first()

    if not stock:
        return jsonify({'error': 'Stock not found for the given batch'}), 404

    return jsonify({
        'available_quantity': stock.quantity
    }), 200

# Create a form for individual order items
class OrderItemForm(FlaskForm):
    sku_id = IntegerField('SKU ID', validators=[DataRequired()])
    quantity = IntegerField('Quantity', validators=[DataRequired()])

# Create the form for the order
class CreateOrderForm(FlaskForm):
    dn_number = StringField('DN Number', validators=[DataRequired()])  # Add DN Number field
    customer_name = StringField('Customer Name', validators=[DataRequired()])
    remarks = TextAreaField('Remarks')
    items = FieldList(FormField(OrderItemForm), min_entries=1)
    submit = SubmitField('Create Order')

@app.route('/orders')
@login_required
def list_orders():
    # Get the search term from query parameters (default to empty string)
    search = request.args.get('search', '')
 
    # Get the current page number from query parameters (default to 1)
    page = request.args.get('page', 1, type=int)
 
    # FIXED: Added sales_order to search filter
    # Create the base query filtering by DN number, customer name, OR sales order
    query = Order.query.filter(
        Order.dn_number.like(f"%{search}%") | 
        Order.customer_name.like(f"%{search}%") |
        Order.sales_order.like(f"%{search}%")  # ← ADDED THIS!
    ).order_by(Order.created_at.desc())  # Sort by most recent orders
 
    # Paginate the results, 10 orders per page
    pagination = query.paginate(page=page, per_page=10, error_out=False)
 
    # Pass paginated orders and search term to the template
    return render_template('list_orders.html', orders=pagination.items, pagination=pagination, search=search)

@app.route('/order/<int:order_id>')
@login_required
def view_order(order_id):
    order = Order.query.get_or_404(order_id)

    # Calculate unique SKUs and total quantity
    unique_skus = {item.sku.material_number for item in order.items}  # Set of unique SKUs
    total_sku = len(unique_skus)  # Number of unique SKUs
    total_quantity = sum(item.quantity for item in order.items)  # Sum of quantities

    # Calculate total weight
    total_weight = sum(item.quantity * item.sku.weight for item in order.items)

    return render_template('view_order.html', order=order, total_sku=total_sku, total_quantity=total_quantity, total_weight=total_weight)

@app.route('/create-order', methods=['GET'])
@login_required
def create_order_page():
    return render_template('create_order.html')

@app.route('/create-order', methods=['POST'])
@login_required
def create_order():
    data = request.get_json()

    # Check if the request has valid data
    if not data or not data.get('items'):
        return jsonify({'error': 'Invalid order data.'}), 400

    # Check if the dn_number already exists
    existing_order = Order.query.filter_by(dn_number=data['dn_number']).first()
    if existing_order:
        return jsonify({'error': f"Order with DN number {data['dn_number']} already exists."}), 400

    # Ensure that address and sales_order are provided
    address = data.get('address')
    sales_order = data.get('sales_order')
    if not address or not sales_order:
        return jsonify({'error': 'Address and Sales Order are required.'}), 400

    # Create a new order with provided details, including current_user.username for created_by
    new_order = Order(
        dn_number=data['dn_number'],
        customer_name=data['customer_name'],
        address=address,
        sales_order=sales_order,
        remarks=data.get('remarks'),
        created_by=current_user.username
    )
    db.session.add(new_order)
    db.session.flush()  # To get the new order's ID

    total_weight = 0  # Initialize total weight

    # Loop through each order item provided in the request
    for item in data['items']:
        material_number = item['material_number']
        batch_number = item['batch_number']
        shipment_number = item.get('shipment_number')
        quantity = int(item['quantity'])
        racking_number = item['racking_number']

        # Find the SKU based on the material number
        sku = SKU.query.filter_by(material_number=material_number).first()
        if not sku:
            return jsonify({'error': f'SKU not found for Material Number: {material_number}'}), 404

        # Calculate weight for the current SKU
        weight_per_item = sku.weight  # Assuming weight field exists in SKU
        total_weight += weight_per_item * quantity  # Add to total weight

        # Normalize shipment_number
        if shipment_number == "N/A" or not shipment_number:
            shipment_number = None

        # Adjust the stock query
        stock_query = Stock.query.filter_by(
            sku_id=sku.id,
            batch_number=batch_number,
            racking_number=racking_number
        )

        if shipment_number:
            # If shipment_number is specified, filter by it
            stock_query = stock_query.filter_by(shipment_number=shipment_number)
        else:
            # Handle cases where shipment_number is None or "N/A"
            stock_query = stock_query.filter(
                (Stock.shipment_number == None) | (Stock.shipment_number == '') | (Stock.shipment_number == 'N/A')
            )

        stock = stock_query.filter(Stock.quantity > 0).first()

        if not stock:
            return jsonify({
                'error': f'No stock found for SKU {material_number}, Batch {batch_number}, '
                         f'Shipment {shipment_number or "N/A"}, Racking {racking_number}'
            }), 404

        if stock.quantity < quantity:
            return jsonify({'error': f'Not enough stock for SKU {material_number}. Available: {stock.quantity}, Requested: {quantity}'}), 400

        # Reduce stock quantity
        stock.quantity -= quantity
        db.session.add(stock)

        # Record the stock history for the GI (Goods Issue)
        stock_history = StockHistory(
            stock_id=stock.id,
            change_type="GI",
            quantity=quantity,
            username=current_user.username,
            remarks=f"Order DN: {data['dn_number']}"
        )
        db.session.add(stock_history)

        # Add the order item with batch number and shipment number
        order_item = OrderItem(
            order_id=new_order.id,
            sku_id=sku.id,
            batch_number=batch_number,
            shipment_number=shipment_number if shipment_number else "N/A",
            quantity=quantity,
            racking_number=racking_number
        )
        db.session.add(order_item)

    db.session.commit()

    return jsonify({
        'message': 'Order created successfully!',
        'order_id': new_order.id,
        'total_weight': total_weight
    }), 201

def generate_picklist_pdf(order):
    file_path = f'picklist_{order.dn_number}.pdf'
    c = canvas.Canvas(file_path, pagesize=letter)
    width, height = letter

    # Order details header
    c.drawString(100, height - 50, f"Pick List for Order DN: {order.sales_order}")
    c.drawString(100, height - 100, f"Pick List for Order DN: {order.dn_number}")
    c.drawString(100, height - 110, f"Customer Name: {order.customer_name}")
    c.drawString(100, height - 120, f"Pick List for Order DN: {order.address}")
    c.drawString(100, height - 130, f"Remarks: {order.remarks}")
    c.drawString(100, height - 150, "Order Items:")

    y = height - 180
    c.drawString(100, y, "Material Number | Product Description | Quantity | Racking Number")
    y -= 20

    # Draw a line under the header
    c.line(100, y, width - 100, y)
    y -= 20

    # Loop through the order items to add to the PDF
    for item in order.items:
        product_description = item.sku.product_description  # Get product description
        racking_number = item.racking_number  # Get racking number
        c.drawString(100, y, f"{item.sku.material_number} | {product_description} | {item.quantity} | {racking_number}")
        y -= 20

    c.save()

    return file_path


def truncate_text_to_fit(pdf, text, max_width):
    """Truncate text to fit within max_width, adding ellipsis if needed"""

    if not text:
        return ''

    # Check if text fits as-is
    if pdf.get_string_width(text) <= max_width:
        return text

    # Add ellipsis and truncate
    ellipsis = '...'
    ellipsis_width = pdf.get_string_width(ellipsis)
    available_width = max_width - ellipsis_width

    # Find max characters that fit
    left = 0
    right = len(text)
    result = ''

    while left < right:
        mid = (left + right + 1) // 2
        test_text = text[:mid]

        if pdf.get_string_width(test_text) <= available_width:
            result = test_text
            left = mid
        else:
            right = mid - 1

    return result + ellipsis if result else text[:1] + ellipsis


# First, install WeasyPrint:
# pip install weasyprint --break-system-packages

from flask import render_template
import pdfkit
import tempfile
import os


@app.route('/print-order/<int:order_id>')
@login_required
def print_order(order_id):
    order = Order.query.get_or_404(order_id)

    # Calculate unique SKUs, total quantity, and total weight
    unique_skus = {item.sku.material_number for item in order.items}
    total_sku = len(unique_skus)
    total_quantity = sum(item.quantity for item in order.items)
    total_weight = sum(item.quantity * item.sku.weight for item in order.items)

    # Render HTML template
    html_content = render_template('order_pdf_template.html',
                                   order=order,
                                   total_sku=total_sku,
                                   total_quantity=total_quantity,
                                   total_weight=total_weight,
                                   logo_path=url_for('static', filename='logo.png', _external=True),
                                   akzonobel_path=url_for('static', filename='akzonobel.png', _external=True))

    # Configure wkhtmltopdf path
    config = pdfkit.configuration(wkhtmltopdf=r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe')

    # Options for PDF generation with page numbers
    options = {
        'page-size': 'A4',
        'margin-top': '15mm',
        'margin-right': '15mm',
        'margin-bottom': '25mm',  # Extra space for footer
        'margin-left': '15mm',
        'encoding': "UTF-8",
        'no-outline': None,
        'enable-local-file-access': None,

        # FOOTER WITH PAGE NUMBERS
        'footer-center': 'Page [page] of [topage]',
        'footer-font-size': '8',
        'footer-spacing': '5',
    }

    # Generate PDF from HTML with options
    pdf = pdfkit.from_string(html_content, False, configuration=config, options=options)

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
        temp_file.write(pdf)
        temp_file_path = temp_file.name

    # Check if this is for printing (inline) or downloading
    if request.args.get('print') == 'true':
        return send_file(temp_file_path,
                         as_attachment=False,
                         download_name=f"order_{order.dn_number}.pdf",
                         mimetype='application/pdf')
    else:
        return send_file(temp_file_path,
                         as_attachment=True,
                         download_name=f"order_{order.dn_number}.pdf",
                         mimetype='application/pdf')

from collections import defaultdict
import re

@app.route('/rack-status')
@login_required
def rack_status():

    all_racks = Racking.query.order_by(Racking.aisle, Racking.level, Racking.position).all()

    # Get occupied racking numbers with qty > 0
    occupied_racking_numbers = {
        r[0] for r in db.session.query(Stock.racking_number)
        .filter(Stock.quantity > 0).distinct().all()
    }

    # Build rack info lookup: racking_number -> rack data
    rack_lookup = {}

    for rack in all_racks:
        is_occupied = rack.racking_number in occupied_racking_numbers
        total_qty = 0
        has_mixed_packsize = False

        if is_occupied:
            stocks = Stock.query.filter(
                Stock.racking_number == rack.racking_number,
                Stock.quantity > 0
            ).all()
            total_qty = sum(s.quantity for s in stocks)

            pack_size_groups = set()
            for s in stocks:
                if s.sku.pack_size is not None:
                    try:
                        pack_size_groups.add(int(float(s.sku.pack_size.size)))
                    except (ValueError, TypeError):
                        pack_size_groups.add(s.sku.pack_size.size)
            has_mixed_packsize = len(pack_size_groups) > 1

        rack_lookup[rack.racking_number] = {
            'is_occupied': is_occupied,
            'total_qty': total_qty,
            'has_mixed_packsize': has_mixed_packsize,
        }

    # Parse rack numbers and group by aisle
    # Format: {Aisle}-{Bay:02d}-{ColLetter}{BinIndex}  e.g. A-06-E2
    aisle_data = defaultdict(lambda: {
        'bays': set(),
        'cols': set(),
        'bins': set(),
        'racks': {}  # { (bay, col, bin): rack_number }
    })

    for rack in all_racks:
        parts = rack.racking_number.split('-')
        if len(parts) != 3:
            continue
        aisle = parts[0]
        bay = int(parts[1])
        col_bin = parts[2]  # e.g. "E2"

        match = re.match(r'([A-Za-z]+)(\d+)', col_bin)
        if not match:
            continue
        col = match.group(1).upper()
        bin_num = int(match.group(2))

        aisle_data[aisle]['bays'].add(bay)
        aisle_data[aisle]['cols'].add(col)
        aisle_data[aisle]['bins'].add(bin_num)   # FIX: store bin_num directly
        aisle_data[aisle]['racks'][(bay, col, bin_num)] = rack.racking_number

    # Sort everything and convert to plain dict
    result = {}
    for aisle in sorted(aisle_data.keys()):
        d = aisle_data[aisle]
        result[aisle] = {
            'bays': sorted(d['bays'], reverse=True),   # 06, 05, 04...
            'cols': sorted(d['cols'], reverse=True),   # E, D, C, B, A
            'bins': sorted(d['bins'], reverse=True),   # 2, 1  (FIX: d['bins'] is already a set of ints)
            'racks': d['racks'],
        }

    total_racks = len(all_racks)
    occupied_count = sum(1 for v in rack_lookup.values() if v['is_occupied'])
    empty_count = total_racks - occupied_count

    return render_template('rack_status.html',
                           aisle_data=result,
                           rack_lookup=rack_lookup,
                           total_racks=total_racks,
                           occupied_count=occupied_count,
                           empty_count=empty_count)

# Add this route to app.py

@app.route('/print-empty-racks')
@login_required
def print_empty_racks():
    all_racks = Racking.query.order_by(Racking.aisle, Racking.level, Racking.position).all()

    # Get occupied racking numbers
    occupied_racking_numbers = {
        r[0] for r in db.session.query(Stock.racking_number)
        .filter(Stock.quantity > 0).distinct().all()
    }

    # Group empty racks by aisle
    from collections import defaultdict
    empty_by_aisle = defaultdict(list)

    for rack in all_racks:
        if rack.racking_number not in occupied_racking_numbers:
            empty_by_aisle[rack.aisle].append(rack.racking_number)

    # Sort aisles and racks
    empty_by_aisle = dict(sorted(empty_by_aisle.items()))

    total_empty = sum(len(v) for v in empty_by_aisle.values())
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    return render_template('print_empty_racks.html',
                           empty_by_aisle=empty_by_aisle,
                           total_empty=total_empty,
                           generated_at=generated_at)

@app.route('/download-picklist/<dn_number>', methods=['GET'])
def download_picklist(dn_number):
    file_path = f'picklist_{dn_number}.pdf'
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        return "Picklist not found", 404


@app.route('/print-order-view/<int:order_id>')
@login_required
def print_order_view(order_id):
    """Render printable HTML view of order"""

    order = Order.query.get_or_404(order_id)

    unique_skus = {item.sku.material_number for item in order.items}
    total_sku = len(unique_skus)
    total_quantity = sum(item.quantity for item in order.items)
    total_weight = sum(item.quantity * item.sku.weight for item in order.items)

    return render_template('print_order_view.html',
                           order=order,
                           total_sku=total_sku,
                           total_quantity=total_quantity,
                           total_weight=total_weight)

@app.route('/cancel-order/<int:order_id>', methods=['GET', 'POST'])
@login_required
def cancel_order(order_id):
    order = Order.query.get_or_404(order_id)

    # Check if the order is already cancelled
    if order.status == 'cancelled':
        return jsonify({'error': 'Order is already cancelled.'}), 400

    if order.status == 'completed':
        return jsonify({'error': 'Cannot cancel a completed order.'}), 400

    # Reverse the Goods Issue (GI)
    for item in order.items:
        # FIXED: Match the EXACT stock record used in the order
        # Must match: sku_id, batch_number, shipment_number, racking_number
        
        # Normalize shipment_number for query (same as create_order)
        shipment_for_query = None if item.shipment_number in (None, '', 'N/A') else item.shipment_number
        
        # Build the query to find the exact stock record
        stock_query = Stock.query.filter_by(
            sku_id=item.sku_id,
            batch_number=item.batch_number,
            racking_number=item.racking_number
        )
        
        # Handle shipment_number matching
        if shipment_for_query is None:
            # Match records with no shipment (None, '', or 'N/A')
            stock_query = stock_query.filter(
                (Stock.shipment_number == None) | 
                (Stock.shipment_number == '') | 
                (Stock.shipment_number == 'N/A')
            )
        else:
            # Match specific shipment number
            stock_query = stock_query.filter_by(shipment_number=shipment_for_query)
        
        stock = stock_query.filter(Stock.quantity > 0).first()
        
        if stock:
            # Stock record exists - add quantity back
            stock.quantity += item.quantity
            db.session.add(stock)
            stock_id_for_history = stock.id
        else:
            # Stock record doesn't exist (maybe was deleted) - create new one
            new_stock = Stock(
                sku_id=item.sku_id,
                quantity=item.quantity,
                batch_number=item.batch_number,
                shipment_number=shipment_for_query,  # Use None for no shipment
                racking_number=item.racking_number,
                remarks='Returned from cancelled order'
            )
            db.session.add(new_stock)
            db.session.flush()  # Get the new stock ID
            stock_id_for_history = new_stock.id

        # Record the stock history for the reversal
        stock_history = StockHistory(
            stock_id=stock_id_for_history,
            change_type="GI Reversal",
            quantity=item.quantity,
            username=current_user.username,
            remarks=f"Cancelled Order DN: {order.dn_number}"
        )
        db.session.add(stock_history)

    # Update the order status
    order.status = 'cancelled'
    db.session.commit()

    return jsonify({'message': f'Order {order.dn_number} has been cancelled successfully.'}), 200


@app.route('/delete-order/<int:order_id>', methods=['POST'])
@login_required
def delete_order(order_id):
    order = Order.query.get_or_404(order_id)

    # Check if the order status is 'cancelled'
    if order.status != 'cancelled':
        return jsonify({'error': 'Only cancelled orders can be deleted.'}), 400

    try:
        # First delete all related order items
        OrderItem.query.filter_by(order_id=order.id).delete()

        # Then delete the order itself
        db.session.delete(order)
        db.session.commit()

        return jsonify({'message': f'Order {order.dn_number} has been deleted successfully.'}), 200
    except Exception as e:
        db.session.rollback()  # Rollback if there's any error
        return jsonify({'error': f'Error deleting order: {str(e)}'}), 500

# Route to enter admin token
@app.route('/admin-token', methods=['GET', 'POST'])
def admin_token():
    if request.method == 'POST':
        token = request.form.get('token')

        # Validate the token
        if token == '3000_at_dunia':
            session['admin_token_valid'] = True
            flash('Token validated! Please proceed to register.', 'success')
            return redirect(url_for('admin_register'))
        else:
            flash('Invalid token!', 'danger')
            return redirect(url_for('admin_token'))

    # Render the token input form
    return render_template('admin_token.html')  # Ensure this template exists

# Route for admin registration
@app.route('/admin-register', methods=['GET', 'POST'])
def admin_register():
    # Check if the token was validated and stored in session
    if not session.get('admin_token_valid'):
        flash('Please enter a valid admin token to access this page.', 'warning')
        return redirect(url_for('admin_token'))

    if request.method == 'POST':
        # Process the admin registration form
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        # Validate password confirmation
        if password != confirm_password:
            flash('Passwords do not match. Please try again.', 'danger')
            return redirect(url_for('admin_register'))

        # Hash the password
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        # Check if the user already exists
        existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
        if existing_user:
            flash('Username or email already exists. Please choose another.', 'danger')
            return redirect(url_for('admin_register'))

        # Automatically assign admin status
        new_admin = User(username=username, email=email, password=hashed_password, is_admin=True)
        db.session.add(new_admin)
        db.session.commit()

        # Clear the token from the session to prevent reuse
        session.pop('admin_token_valid', None)

        # Notify success and redirect to login
        flash('Admin registration successful! You can now log in as an admin.', 'success')
        return redirect(url_for('login'))

    # Render the admin registration form if the token is valid
    return render_template('admin_register.html')  # Update this template as per your provided template


@app.route('/admin/approve-users', methods=['GET', 'POST'])
@login_required
def approve_users():
    # Ensure only admins can access this route
    if not current_user.is_admin:
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('dashboard'))

    # Query all users (approved and unapproved)
    users = User.query.all()

    # Handle POST request for approving users
    if request.method == 'POST':
        user_id = request.form.get('user_id')
        user = User.query.get(user_id)
        if user and not user.is_approved:
            user.is_approved = True
            db.session.commit()
            flash(f'User {user.username} has been approved!', 'success')
        return redirect(url_for('approve_users'))

    return render_template('approve_users.html', users=users)


@app.route('/logs', methods=['GET'])
@login_required
def view_logs():

    logs_dir = os.path.join(os.getcwd(), 'logs')

    # Ensure the directory exists, create it if it doesn't
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
        flash('Logs directory created as it did not exist.', 'info')
        return render_template('view_logs.html', log_files=[])

    # Get the list of log files in the directory
    log_files = [f for f in os.listdir(logs_dir) if os.path.isfile(os.path.join(logs_dir, f))]

    return render_template('view_logs.html', log_files=log_files)


@app.route('/download-log/<filename>', methods=['GET'])
@login_required
def download_log(filename):

    logs_dir = os.path.join(os.getcwd(), 'logs')
    file_path = os.path.join(logs_dir, filename)

    # Check if the filename contains any path traversal characters
    if '..' in filename or filename.startswith('/'):
        flash('Invalid file path.', 'danger')
        return redirect(url_for('view_logs'))

    # Check if the file exists before sending it
    if not os.path.exists(file_path):
        flash('File not found.', 'danger')
        return redirect(url_for('view_logs'))

    # Send the file to the user
    return send_file(file_path, as_attachment=True, download_name=filename)


@app.route('/delete-log/<filename>', methods=['POST'])
@login_required
def delete_log(filename):
    logs_dir = os.path.abspath(os.path.join(os.getcwd(), 'logs'))
    file_path = os.path.abspath(os.path.join(logs_dir, filename))

    # Validate the filename to prevent directory traversal attacks
    if not file_path.startswith(logs_dir + os.sep):
        flash('Invalid file path.', 'danger')
        return redirect(url_for('view_logs'))

    # Check if the file exists
    if not os.path.exists(file_path):
        flash('File not found.', 'danger')
        return redirect(url_for('view_logs'))

    try:
        os.remove(file_path)
        flash(f'Log file "{filename}" has been deleted.', 'success')
    except Exception as e:
        flash(f'An error occurred while deleting the log file: {str(e)}', 'danger')

    return redirect(url_for('view_logs'))

@app.route('/edit-pack-size/<int:pack_id>', methods=['GET', 'POST'])
@login_required
def edit_pack_size(pack_id):
    pack_size = PackSize.query.get_or_404(pack_id)

    if request.method == 'POST':
        pack_size.size = request.form['size']
        pack_size.max_capacity = request.form['max_capacity']
        db.session.commit()
        flash('Pack size updated successfully!', 'success')
        return redirect(url_for('add_sku_html'))

    return render_template('edit_pack_size.html', pack_size=pack_size)


@app.route('/delete-pack-size/<int:pack_id>', methods=['POST'])
@login_required
def delete_pack_size(pack_id):
    pack_size = PackSize.query.get_or_404(pack_id)

    # Check if the pack size is being used by any SKU
    sku_using_pack_size = SKU.query.filter_by(pack_size_id=pack_id).first()
    if sku_using_pack_size:
        flash('Cannot delete pack size because it is being used by some SKUs.', 'error')
        return redirect(url_for('add_sku_html'))

    # If no SKUs are using the pack size, proceed with deletion
    db.session.delete(pack_size)
    db.session.commit()
    flash('Pack size deleted successfully!', 'success')
    return redirect(url_for('add_sku_html'))

@app.route('/update-daily-rack-count', methods=['GET'])
@login_required
def update_daily_rack_count():
    # Check if the user is an admin to allow them to update
    if not current_user.is_admin:
        flash('You are not authorized to perform this action.', 'danger')
        return redirect(url_for('dashboard'))

    today = datetime.now(pytz.timezone('Asia/Kuala_Lumpur')).date()
    existing_entry = DailyRackCount.query.filter_by(date=today).first()

    if not existing_entry:
        # Query for the current occupied racks count
        # FIXED: Added .filter(Stock.quantity > 0) to exclude zero-quantity racks
        occupied_racks = (
            db.session.query(Stock.racking_number)
            .filter(Stock.quantity > 0)  # ← ADDED THIS LINE!
            .distinct()
            .count()
        )

        # Insert a new record for today's occupied rack count
        new_entry = DailyRackCount(date=today, occupied_racks=occupied_racks)
        db.session.add(new_entry)
        db.session.commit()
        flash('Daily rack count has been successfully updated!', 'success')
    else:
        flash('Daily rack count for today already exists.', 'info')

    return redirect(url_for('dashboard'))

@app.route('/edit-order/<int:order_id>', methods=['GET'])
@login_required
def edit_order(order_id):
    order = Order.query.get_or_404(order_id)
    order_items = OrderItem.query.filter_by(order_id=order.id).all()
    return render_template('edit_order.html', order=order, order_items=order_items)

@app.route('/update_order/<int:order_id>', methods=['POST'])
@login_required
def update_order(order_id):
    data = request.get_json(force=True) or {}
    order = Order.query.get_or_404(order_id)

    # Header only — do not touch stock or order items here
    order.customer_name   = data.get('customer_name', order.customer_name)
    order.address         = data.get('address', order.address)
    order.sales_order     = data.get('sales_order', order.sales_order)
    order.remarks         = data.get('remarks', order.remarks)
    order.last_updated_by = current_user.username

    db.session.commit()
    return jsonify({'message': 'Order updated successfully!'}), 200

@app.route('/orders/<int:order_id>/add-item', methods=['POST'])
@login_required
def add_order_item(order_id):
    data = request.get_json(force=True) or {}
    material = (data.get('material_number') or '').strip()
    batch    = (data.get('batch_number') or '').strip()
    rack     = (data.get('racking_number') or '').strip()
    ship_in  = data.get('shipment_number')
    qty      = int(data.get('quantity') or 0)

    if not (material and batch and rack and qty > 0):
        return jsonify({'error': 'material_number, batch_number, racking_number and positive quantity are required'}), 400

    order = Order.query.get_or_404(order_id)
    sku = SKU.query.filter_by(material_number=material).first_or_404()

    # Normalize shipment for Stock filtering (None = no shipment),
    # but for OrderItem we will store "N/A" if missing.
    ship_for_stock = None if ship_in in (None, '', 'N/A') else ship_in
    ship_for_item  = ship_in if ship_in not in (None, '', 'N/A') else 'N/A'

    # Check stock availability
    q = Stock.query.filter_by(sku_id=sku.id, batch_number=batch, racking_number=rack)
    if ship_for_stock is None:
        q = q.filter(or_(Stock.shipment_number == None,
                         Stock.shipment_number == "",
                         Stock.shipment_number == "N/A"))
    else:
        q = q.filter(Stock.shipment_number == ship_for_stock)

    total = sum(int(s.quantity or 0) for s in q.all())
    if total < qty:
        return jsonify({'error': f'Not enough stock. Available {total}'}), 400

    # Deduct FIFO
    need = qty
    for s in q.order_by(Stock.id.asc()).all():
        take = min(int(s.quantity), need)
        if take <= 0: continue
        s.quantity = int(s.quantity) - take
        need -= take
        db.session.add(StockHistory(
            stock_id=s.id,
            change_type=f"GI - picked for order {order_id}",
            quantity=take,
            username=current_user.username,
            remarks=f"DN {order.dn_number}"
        ))
        if need == 0:
            break

    # Create OrderItem with "N/A" when no shipment
    item = OrderItem(
        order_id=order.id,
        sku_id=sku.id,
        quantity=qty,
        batch_number=batch,
        shipment_number=ship_for_item,   # <-- never None
        racking_number=rack
    )
    db.session.add(item)
    db.session.commit()

    return jsonify({'success': True, 'item_id': item.id, 'weight_per_unit': (sku.weight or 0)}), 200

@app.route('/orders/<int:order_id>/adjust-item/<int:item_id>', methods=['POST'])
@login_required
def adjust_order_item(order_id, item_id):
    data = request.get_json(force=True) or {}
    new_qty = int(data.get('new_qty') or 0)
    if new_qty < 0:
        return jsonify({'error': 'new_qty must be >= 0'}), 400

    order = Order.query.get_or_404(order_id)
    item  = OrderItem.query.filter_by(id=item_id, order_id=order.id).first_or_404()

    delta = new_qty - int(item.quantity or 0)
    if delta == 0:
        return jsonify({'success': True, 'delta': 0})

    # For Stock, normalize "N/A" to no-shipment
    ship_for_stock = None if item.shipment_number in (None, '', 'N/A') else item.shipment_number

    q = Stock.query.filter_by(
        sku_id=item.sku_id,
        batch_number=item.batch_number,
        racking_number=item.racking_number
    )
    if ship_for_stock is None:
        q = q.filter(or_(Stock.shipment_number == None,
                         Stock.shipment_number == "",
                         Stock.shipment_number == "N/A"))
    else:
        q = q.filter(Stock.shipment_number == ship_for_stock)

    if delta > 0:
        # Need to deduct more
        total = sum(int(s.quantity or 0) for s in q.all())
        if total < delta:
            return jsonify({'error': f'Not enough stock. Need {delta}, available {total}'}), 400

        need = delta
        for s in q.order_by(Stock.id.asc()).all():
            take = min(int(s.quantity), need)
            if take <= 0: continue
            s.quantity = int(s.quantity) - take
            need -= take
            db.session.add(StockHistory(
                stock_id=s.id,
                change_type=f"GI - adjust up order {order_id}",
                quantity=take,
                username=current_user.username,
                remarks=f"DN {order.dn_number}"
            ))
            if need == 0:
                break
    else:
        # Return -delta to stock
        give_back = -delta
        s = q.first()
        if s:
            s.quantity = int(s.quantity or 0) + give_back
            stock_id = s.id
        else:
            new_s = Stock(
                sku_id=item.sku_id,
                quantity=give_back,
                batch_number=item.batch_number,
                shipment_number=None,  # keep Stock side as null for "no shipment"
                racking_number=item.racking_number,
                remarks='returned_by_order_adjust'
            )
            db.session.add(new_s)
            db.session.flush()
            stock_id = new_s.id

        db.session.add(StockHistory(
            stock_id=stock_id,
            change_type=f"GR - adjust down order {order_id}",
            quantity=give_back,
            username=current_user.username,
            remarks=f"DN {order.dn_number}"
        ))

    item.quantity = new_qty
    db.session.commit()
    return jsonify({'success': True, 'delta': delta})


@app.route('/orders/<int:order_id>/remove-item/<int:item_id>', methods=['POST'])
@login_required
def remove_order_item(order_id, item_id):
    order = Order.query.get_or_404(order_id)
    item  = OrderItem.query.filter_by(id=item_id, order_id=order.id).first_or_404()
    qty   = int(item.quantity or 0)

    # For Stock, treat "N/A" as no-shipment
    ship_for_stock = None if item.shipment_number in (None, '', 'N/A') else item.shipment_number

    q = Stock.query.filter_by(
        sku_id=item.sku_id,
        batch_number=item.batch_number,
        racking_number=item.racking_number
    )
    if ship_for_stock is None:
        q = q.filter(or_(Stock.shipment_number == None,
                         Stock.shipment_number == "",
                         Stock.shipment_number == "N/A"))
    else:
        q = q.filter(Stock.shipment_number == ship_for_stock)

    s = q.first()
    if s:
        s.quantity = int(s.quantity or 0) + qty
        stock_id = s.id
    else:
        new_s = Stock(
            sku_id=item.sku_id,
            quantity=qty,
            batch_number=item.batch_number,
            shipment_number=ship_for_stock,  # None here means real NULL in Stock
            racking_number=item.racking_number,
            remarks='returned_by_order_remove'
        )
        db.session.add(new_s)
        db.session.flush()
        stock_id = new_s.id

    db.session.add(StockHistory(
        stock_id=stock_id,
        change_type=f"GR - remove line order {order_id}",
        quantity=qty,
        username=current_user.username,
        remarks=f"DN {order.dn_number}"
    ))

    db.session.delete(item)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/toggle_order_status/<int:order_id>', methods=['POST'])
@login_required
def toggle_order_status(order_id):
    order = Order.query.get_or_404(order_id)

    data = request.get_json()
    current_status = data.get('current_status')

    if current_status not in ['active', 'completed']:
        return jsonify({'error': 'Invalid order status.'}), 400

    if current_status == 'active':
        # Change status to 'completed'
        order.status = 'completed'
        message = 'Order marked as completed successfully.'
    elif current_status == 'completed':
        # Change status back to 'active'
        order.status = 'active'
        message = 'Order re-opened successfully.'
    else:
        return jsonify({'error': 'Unsupported order status.'}), 400

    db.session.commit()

    # Log the new status
    logging.info(f"Order ID {order_id} status changed to {order.status}")

    return jsonify({'message': message, 'new_status': order.status}), 200


# UPDATED CYCLE COUNT ROUTE - Groups by Racking/Bay
# Replace the existing /cycle-count route with this

@app.route('/cycle-count', methods=['GET'])
@login_required
def cycle_count():
    from datetime import datetime

    # Get filters
    selected_racking = request.args.get('racking_number', 'all')
    include_zero = request.args.get('include_zero', 'no')
    sku_filter = request.args.get('sku_filter', '').strip()        # ← new
    batch_filter = request.args.get('batch_filter', '').strip()    # ← new

    # Get all racking numbers
    all_rackings = Racking.query.order_by(Racking.racking_number).all()

    # Build racking list
    racking_list = []
    rack_letters = set()
    for r in all_rackings:
        rack_letter = r.racking_number.split('-')[0] if '-' in r.racking_number else r.racking_number[0]
        rack_letters.add(rack_letter)
    for letter in sorted(rack_letters):
        racking_list.append(letter)
    for r in all_rackings:
        racking_list.append(r.racking_number)

    # Query stocks
    query = db.session.query(
        Stock.id.label('stock_id'),
        SKU.material_number,
        SKU.product_description,
        Stock.batch_number,
        Stock.shipment_number,
        Stock.racking_number,
        Stock.quantity,
        Stock.remarks
    ).join(SKU, Stock.sku_id == SKU.id)

    # Apply racking filter
    if selected_racking != 'all':
        if len(selected_racking) == 1:
            query = query.filter(Stock.racking_number.like(f'{selected_racking}-%'))
        else:
            query = query.filter(Stock.racking_number == selected_racking)

    # Apply zero stock filter
    if include_zero == 'no':
        query = query.filter(Stock.quantity > 0)

    # Apply SKU filter ← new
    if sku_filter:
        query = query.filter(SKU.material_number.ilike(f'%{sku_filter}%'))

    # Apply batch filter ← new
    if batch_filter:
        query = query.filter(Stock.batch_number.ilike(f'%{batch_filter}%'))

    # Order
    query = query.order_by(Stock.racking_number, SKU.material_number, Stock.batch_number)
    stocks = query.all()

    # Group by racking number
    racking_stocks = {}
    for stock in stocks:
        rack_num = stock.racking_number
        if rack_num not in racking_stocks:
            racking_stocks[rack_num] = []
        racking_stocks[rack_num].append({
            'stock_id': stock.stock_id,
            'material_number': stock.material_number,
            'product_description': stock.product_description,
            'batch_number': stock.batch_number,
            'shipment_number': stock.shipment_number,
            'racking_number': stock.racking_number,
            'quantity': stock.quantity,
            'remarks': stock.remarks
        })

    return render_template('cycle_count_print.html',
                           racking_stocks=racking_stocks,
                           racking_list=racking_list,
                           selected_racking=selected_racking,
                           include_zero=include_zero,
                           sku_filter=sku_filter,          # ← new
                           batch_filter=batch_filter,      # ← new
                           count_date=datetime.now().strftime('%Y-%m-%d'),
                           total_stock_lines=len(stocks))


@app.route('/cycle-count/perform')
@login_required
def cycle_count_perform():
    """Form to enter actual counted quantities"""
    include_zero = request.args.get('include_zero', 'yes')
    selected_racking = request.args.get('racking_number', 'all')
    selected_sku = request.args.get('material_number', 'all')

    # Get all racking numbers for the dropdown
    all_rackings = db.session.query(Stock.racking_number) \
        .distinct() \
        .filter(Stock.racking_number != None) \
        .filter(Stock.racking_number != '') \
        .order_by(Stock.racking_number) \
        .all()
    racking_list = [r[0] for r in all_rackings]

    # Get all SKUs for the dropdown
    all_skus = SKU.query.order_by(SKU.material_number).all()
    sku_list = [(sku.material_number, sku.product_description) for sku in all_skus]

    # Query to get all SKUs with their stock details
    query = db.session.query(
        SKU.id,
        SKU.material_number,
        SKU.product_description,
        Stock.id.label('stock_id'),
        Stock.batch_number,
        Stock.shipment_number,
        Stock.racking_number,
        Stock.quantity,
        Stock.remarks
    ).outerjoin(Stock, SKU.id == Stock.sku_id)

    # Filter by racking number if specific racking selected
    # FIXED: Added support for whole rack selection (like "A" shows all "A-*")
    if selected_racking != 'all':
        # If single letter (A, B, C), show all bays in that rack
        if len(selected_racking) == 1:
            query = query.filter(Stock.racking_number.like(f'{selected_racking}-%'))
        else:
            # Specific bay
            query = query.filter(Stock.racking_number == selected_racking)

    # Filter by SKU if specific SKU selected
    if selected_sku != 'all':
        query = query.filter(SKU.material_number == selected_sku)

    # Filter based on include_zero parameter
    if include_zero == 'no':
        query = query.filter(Stock.quantity > 0)

    stocks = query.order_by(SKU.material_number, Stock.batch_number).all()

    # Group stocks by SKU for better display
    sku_stocks = defaultdict(list)
    for stock in stocks:
        sku_stocks[stock.material_number].append(stock)

    return render_template('cycle_count_perform.html',
                           sku_stocks=sku_stocks,
                           include_zero=include_zero,
                           racking_list=racking_list,
                           selected_racking=selected_racking,
                           sku_list=sku_list,
                           selected_sku=selected_sku)


@app.route('/cycle-count/update', methods=['POST'])
@login_required
def cycle_count_update():
    """Update stock quantities based on cycle count"""
    try:
        data = request.json
        updates = data.get('updates', [])
        
        if not updates:
            return jsonify({'error': 'No updates provided'}), 400
        
        updated_count = 0
        adjustments = []
        
        for update in updates:
            stock_id = update.get('stock_id')
            counted_qty = int(update.get('counted_qty', 0))
            
            if stock_id is None:
                continue
            
            # Get the stock record
            stock = Stock.query.get(stock_id)
            if not stock:
                continue
            
            # Calculate the difference
            old_qty = stock.quantity
            difference = counted_qty - old_qty
            
            if difference != 0:
                # Update the stock quantity
                stock.quantity = counted_qty
                
                # Create stock history record
                change_type = 'Cycle Count Adjustment'
                stock_history = StockHistory(
                    stock_id=stock.id,
                    change_type=change_type,
                    quantity=abs(difference),
                    username=current_user.username,
                    remarks=f'Cycle count: {old_qty} → {counted_qty} ({"+" if difference > 0 else ""}{difference})'
                )
                db.session.add(stock_history)
                
                adjustments.append({
                    'material_number': stock.sku.material_number,
                    'product_description': stock.sku.product_description,
                    'batch_number': stock.batch_number,
                    'old_qty': old_qty,
                    'new_qty': counted_qty,
                    'difference': difference
                })
                
                updated_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'updated_count': updated_count,
            'adjustments': adjustments
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

def get_local_ip():
    """Get the local IP address of this machine"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to a public DNS server (doesn't actually send data)
        s.connect(('8.8.8.8', 80))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

# Add this route to app.py (after the transfer_stock route)

@app.route('/mass-transfer-rack', methods=['GET'])
@login_required
def mass_transfer_rack():
    """Display page for mass transferring all items from one rack to another"""
    # Get all racking numbers for dropdowns
    all_rackings = Racking.query.order_by(Racking.racking_number).all()
    racking_list = [r.racking_number for r in all_rackings]
    
    return render_template('mass_transfer_rack.html', racking_list=racking_list)


@app.route('/get-rack-contents/<racking_number>', methods=['GET'])
@login_required
def get_rack_contents(racking_number):
    """Get all stock items in a specific rack"""
    try:
        # Get all stock items in this rack with quantity > 0
        stocks = Stock.query.filter_by(racking_number=racking_number).filter(Stock.quantity > 0).all()
        
        if not stocks:
            return jsonify({'error': 'No items found in this rack'}), 404
        
        # Build response with stock details
        items = []
        total_quantity = 0
        
        for stock in stocks:
            items.append({
                'stock_id': stock.id,
                'material_number': stock.sku.material_number,
                'product_description': stock.sku.product_description,
                'batch_number': stock.batch_number,
                'shipment_number': stock.shipment_number or 'N/A',
                'quantity': stock.quantity,
                'remarks': stock.remarks or ''
            })
            total_quantity += stock.quantity
        
        return jsonify({
            'success': True,
            'items': items,
            'total_quantity': total_quantity,
            'item_count': len(items)
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Add these routes to app.py for cycle count search functionality

@app.route('/search-sku-cycle-count', methods=['POST'])
@login_required
def search_sku_cycle_count():
    """Search SKU by material number OR product description for cycle count"""
    try:
        data = request.get_json()
        search_term = (data.get('search_term') or '').strip()

        if not search_term:
            return jsonify({'results': []}), 200

        # Search by material_number OR product_description (case-insensitive)
        # Join with Stock to get all SKUs that have stock entries
        skus = db.session.query(SKU).join(Stock).filter(
            (SKU.material_number.like(f'%{search_term}%')) |
            (SKU.product_description.like(f'%{search_term}%'))
        ).distinct().limit(10).all()

        results = []
        for sku in skus:
            results.append({
                'material_number': sku.material_number,
                'product_description': sku.product_description,
                'display': f"{sku.material_number} - {sku.product_description}"
            })

        return jsonify({'results': results}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/search-racking', methods=['POST'])
@login_required
def search_racking():
    """Search racking numbers"""
    try:
        data = request.get_json()
        search_term = (data.get('search_term') or '').strip()
        
        if not search_term:
            return jsonify({'results': []}), 200
        
        # Search racking numbers (case-insensitive)
        rackings = Racking.query.filter(
            Racking.racking_number.like(f'%{search_term}%')
        ).limit(10).all()
        
        results = []
        for racking in rackings:
            results.append({
                'racking_number': racking.racking_number,
                'aisle': racking.aisle or '',
                'display': f"{racking.racking_number} (Aisle: {racking.aisle or 'N/A'})"
            })
        
        return jsonify({'results': results}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Add this route to app.py for Manual GR (around line 792, after search-sku)

@app.route('/search-sku-for-gr', methods=['POST'])
@login_required
def search_sku_for_gr():
    """Search SKU by material number OR product description for Goods Receiving
    This version does NOT filter by stock quantity - allows receiving new items"""
    try:
        data = request.get_json()
        search_term = (data.get('search_term') or '').strip()
        
        if not search_term:
            return jsonify({'results': []}), 200
        
        # Search by material_number OR product_description (case-insensitive)
        # NO stock filter - GR needs to receive items that have zero stock!
        skus = SKU.query.filter(
            (SKU.material_number.like(f'%{search_term}%')) | 
            (SKU.product_description.like(f'%{search_term}%'))
        ).limit(10).all()
        
        results = []
        for sku in skus:
            # Get example shipment number if any stock exists
            example_stock = Stock.query.filter_by(sku_id=sku.id).first()
            example_shipment = example_stock.shipment_number if example_stock else None
            
            results.append({
                'material_number': sku.material_number,
                'product_description': sku.product_description,
                'example_shipment': example_shipment,
                'display': f"{sku.material_number} - {sku.product_description}"
            })
        
        return jsonify({'results': results}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
                
@app.route('/execute-mass-transfer', methods=['POST'])
@login_required
def execute_mass_transfer():
    """Transfer all items from source rack to destination rack"""
    try:
        data = request.get_json()
        source_rack = data.get('source_rack')
        dest_rack = data.get('dest_rack')
        
        if not source_rack or not dest_rack:
            return jsonify({'error': 'Source and destination racks are required'}), 400
        
        if source_rack == dest_rack:
            return jsonify({'error': 'Source and destination racks cannot be the same'}), 400
        
        # Verify destination rack exists
        dest_racking = Racking.query.filter_by(racking_number=dest_rack).first()
        if not dest_racking:
            return jsonify({'error': 'Destination rack not found'}), 404
        
        # Get all stock items from source rack
        stocks = Stock.query.filter_by(racking_number=source_rack).filter(Stock.quantity > 0).all()
        
        if not stocks:
            return jsonify({'error': 'No items found in source rack'}), 404
        
        transfer_count = 0
        total_quantity_transferred = 0
        
        # Transfer each stock item
        for stock in stocks:
            quantity = stock.quantity
            
            # Check if item with same details already exists in destination
            existing_stock = Stock.query.filter_by(
                sku_id=stock.sku_id,
                batch_number=stock.batch_number,
                shipment_number=stock.shipment_number,
                racking_number=dest_rack,
                remarks=stock.remarks
            ).first()
            
            if existing_stock:
                # Merge into existing stock
                existing_stock.quantity += quantity
                new_stock_id = existing_stock.id
            else:
                # Create new stock entry in destination rack
                new_stock = Stock(
                    sku_id=stock.sku_id,
                    quantity=quantity,
                    batch_number=stock.batch_number,
                    shipment_number=stock.shipment_number,
                    racking_number=dest_rack,
                    remarks=stock.remarks
                )
                db.session.add(new_stock)
                db.session.flush()
                new_stock_id = new_stock.id
            
            # Create stock history for outgoing (from source)
            history_out = StockHistory(
                stock_id=stock.id,
                change_type=f'Mass transfer to {dest_rack}',
                quantity=-quantity,
                username=current_user.username,
                remarks=f'Mass transfer from {source_rack} to {dest_rack}'
            )
            db.session.add(history_out)
            
            # Create stock history for incoming (to destination)
            history_in = StockHistory(
                stock_id=new_stock_id,
                change_type=f'Mass transfer from {source_rack}',
                quantity=quantity,
                username=current_user.username,
                remarks=f'Mass transfer from {source_rack} to {dest_rack}'
            )
            db.session.add(history_in)
            
            # FIXED: Set quantity to 0 instead of deleting
            # This preserves stock_history foreign key relationships
            stock.quantity = 0
            db.session.add(stock)
            
            transfer_count += 1
            total_quantity_transferred += quantity
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Successfully transferred {transfer_count} items ({total_quantity_transferred} total quantity) from {source_rack} to {dest_rack}',
            'transfer_count': transfer_count,
            'total_quantity': total_quantity_transferred
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Error during mass transfer: {str(e)}'}), 500


@app.route('/bulk-transfer-racking', methods=['GET'])
@login_required
def bulk_transfer_racking():
    """Display page for bulk transfer to single racking"""

    # Get all stocks with quantity > 0
    stocks = db.session.query(Stock).join(SKU).filter(Stock.quantity > 0).all()

    # Get all racking numbers for destination dropdown
    racking_numbers = Racking.query.order_by(Racking.racking_number).all()

    return render_template('bulk_transfer_racking.html',
                           stocks=stocks,
                           racking_numbers=racking_numbers)

BULK_TRANSFER_MAX_LINES = 10 

@app.route('/bulk-transfer-racking/execute', methods=['POST'])
@login_required
def execute_bulk_transfer():
    """Execute bulk transfer of multiple stocks to one destination - WITH SAFETY CHECKS"""
 
    try:
        data = request.get_json()
        transfers = data.get('transfers', [])
        destination_racking = data.get('destination_racking')
        force = data.get('force', False)   # frontend may pass force=true after warning
 
        if not transfers or not destination_racking:
            return jsonify({'error': 'Missing transfer data or destination'}), 400
 
        # ── SAFETY 1: Line limit ─────────────────────────────────────────
        if len(transfers) > BULK_TRANSFER_MAX_LINES:
            return jsonify({
                'error': f'Too many lines! Bulk transfer is limited to '
                         f'{BULK_TRANSFER_MAX_LINES} stock lines per operation. '
                         f'You selected {len(transfers)}. '
                         f'Split into smaller transfers.'
            }), 400
 
        # Validate destination racking exists
        dest_rack = Racking.query.filter_by(racking_number=destination_racking).first()
        if not dest_rack:
            return jsonify({'error': f'Destination racking {destination_racking} not found'}), 400
 
        # ── SAFETY 2: Capacity check BEFORE moving anything ─────────────
        # Current quantity at destination
        dest_stocks = Stock.query.filter_by(racking_number=destination_racking).all()
        current_dest_qty = sum(s.quantity for s in dest_stocks)
 
        # Total incoming quantity
        total_incoming = 0
        incoming_caps = []   # capacities of incoming SKUs
        pack_size_groups = set()
 
        # Include existing dest stock pack groups
        import re as _re
        for s in dest_stocks:
            if s.quantity > 0 and s.sku and s.sku.pack_size and s.sku.pack_size.size:
                m = _re.search(r'\d+\.?\d*', str(s.sku.pack_size.size))
                if m:
                    pack_size_groups.add(int(float(m.group())))
 
        for transfer in transfers:
            stock_id = transfer.get('stock_id')
            quantity = int(transfer.get('quantity', 0))
            if quantity <= 0:
                continue
            total_incoming += quantity
 
            src = Stock.query.get(stock_id)
            if src and src.sku and src.sku.pack_size:
                if src.sku.pack_size.max_capacity:
                    try:
                        incoming_caps.append(int(src.sku.pack_size.max_capacity))
                    except Exception:
                        pass
                if src.sku.pack_size.size:
                    m = _re.search(r'\d+\.?\d*', str(src.sku.pack_size.size))
                    if m:
                        pack_size_groups.add(int(float(m.group())))
 
        # Capacity = the incoming SKU's pack capacity (use max of incoming if mixed)
        if incoming_caps:
            max_capacity = max(incoming_caps)
            total_after = current_dest_qty + total_incoming
            if total_after > max_capacity:
                return jsonify({
                    'error': f'CAPACITY EXCEEDED for {destination_racking}!\n'
                             f'Max capacity: {max_capacity}\n'
                             f'Currently in rack: {current_dest_qty}\n'
                             f'Trying to add: {total_incoming}\n'
                             f'Total would be: {total_after}\n\n'
                             f'This transfer is BLOCKED to protect your warehouse data.'
                }), 400
 
        # ── SAFETY 3: Mixed pack size warning ────────────────────────────
        if len(pack_size_groups) > 1 and not force:
            return jsonify({
                'warning': f'Mixed pack sizes detected: {sorted(pack_size_groups)}L in '
                           f'{destination_racking}. Send force=true to proceed anyway.',
                'requires_confirmation': True
            }), 409
 
        # ── Execute transfers (original logic) ───────────────────────────
        successful_transfers = []
        failed_transfers = []
 
        for transfer in transfers:
            stock_id = transfer.get('stock_id')
            quantity = int(transfer.get('quantity', 0))
 
            if quantity <= 0:
                continue
 
            source_stock = Stock.query.get(stock_id)
            if not source_stock:
                failed_transfers.append({'stock_id': stock_id, 'reason': 'Stock not found'})
                continue
 
            if source_stock.quantity < quantity:
                failed_transfers.append({
                    'stock_id': stock_id,
                    'material': source_stock.sku.material_number,
                    'reason': f'Insufficient quantity (available: {source_stock.quantity})'
                })
                continue
 
            existing_dest = Stock.query.filter_by(
                sku_id=source_stock.sku_id,
                batch_number=source_stock.batch_number,
                shipment_number=source_stock.shipment_number,
                racking_number=destination_racking
            ).first()
 
            if existing_dest:
                existing_dest.quantity += quantity
            else:
                new_stock = Stock(
                    sku_id=source_stock.sku_id,
                    batch_number=source_stock.batch_number,
                    shipment_number=source_stock.shipment_number,
                    racking_number=destination_racking,
                    quantity=quantity,
                    remarks=source_stock.remarks,
                    timestamp=datetime.now()
                )
                db.session.add(new_stock)
 
            source_stock.quantity -= quantity
 
            history = StockHistory(
                stock_id=source_stock.id,
                change_type='bulk_transfer',
                quantity=-quantity,
                username=current_user.username,
                timestamp=datetime.now(),
                remarks=f'Bulk transferred {quantity} units from {source_stock.racking_number} to {destination_racking}'
            )
            db.session.add(history)
 
            successful_transfers.append({
                'material': source_stock.sku.material_number,
                'batch': source_stock.batch_number,
                'quantity': quantity,
                'from': source_stock.racking_number,
                'to': destination_racking
            })
 
        db.session.commit()
 
        return jsonify({
            'success': True,
            'message': f'Successfully transferred {len(successful_transfers)} items',
            'successful': successful_transfers,
            'failed': failed_transfers
        })
 
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/search-stocks-for-transfer', methods=['GET'])
@login_required
def search_stocks_for_transfer():
    """Search stocks for bulk transfer"""

    search_term = request.args.get('search', '').strip()

    query = db.session.query(Stock).join(SKU).filter(Stock.quantity > 0)

    if search_term:
        query = query.filter(
            or_(
                SKU.material_number.ilike(f"%{search_term}%"),
                SKU.product_description.ilike(f"%{search_term}%"),
                Stock.batch_number.ilike(f"%{search_term}%"),
                Stock.racking_number.ilike(f"%{search_term}%")
            )
        )

    stocks = query.all()

    stocks_data = [{
        'id': stock.id,
        'material_number': stock.sku.material_number,
        'product_description': stock.sku.product_description,
        'batch_number': stock.batch_number,
        'racking_number': stock.racking_number,
        'quantity': stock.quantity
    } for stock in stocks]

    return jsonify({'stocks': stocks_data})

if __name__ == '__main__':
    from zeroconf import ServiceInfo, Zeroconf
    import socket
    import random
    
    local_ip = get_local_ip()
    port = 5000
    
    # Initialize Zeroconf
    zeroconf = Zeroconf()
    
    # Create UNIQUE service name with random ID to avoid conflicts
    unique_id = random.randint(1000, 9999)
    service_type = "_http._tcp.local."
    service_name = f"WMS-Warehouse-Management-{unique_id}._http._tcp.local."
    
    service_info = ServiceInfo(
        service_type,
        service_name,
        addresses=[socket.inet_aton(local_ip)],
        port=port,
        properties={'path': '/'},
        server="wms-server.local."
    )
    
    # Register the service with error handling
    print("\n" + "="*60)
    print("🚀 WMS Flask Server Starting...")
    print("="*60)
    print(f"📍 Server accessible at:")
    print(f"   • Local:   http://127.0.0.1:{port}")
    print(f"   • Network: http://{local_ip}:{port}")
    print(f"   • mDNS:    http://wms-server.local:{port}")
    print("="*60)
    
    try:
        print("📢 Broadcasting service on network...")
        zeroconf.register_service(service_info)
        print(f"   ✅ Service registered as: {service_name}")
        print("   Other computers can auto-discover this server!")
    except Exception as e:
        print(f"   ⚠️  Could not register mDNS service: {e}")
        print("   Server will still work, but won't auto-discover on network.")
    
    print("="*60 + "\n")
    
    try:
        app.run(host='0.0.0.0', port=port, debug=True)
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down server...")
    finally:
        print("📢 Unregistering service...")
        try:
            zeroconf.unregister_service(service_info)
        except:
            pass
        zeroconf.close()
        print("✅ Server stopped gracefully\n")