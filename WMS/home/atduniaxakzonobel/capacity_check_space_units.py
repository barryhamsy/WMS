# Updated Racking Capacity Check with Space Units
# Replace the check-racking-capacity route in app.py with this

@app.route('/check-racking-capacity', methods=['POST'])
def check_racking_capacity():
    """
    Check if incoming stock fits in racking using space units calculation.
    Allows mixing different pack sizes on same pallet/rack.
    """
    data = request.get_json() or {}
    racking_number   = (data.get('racking_number') or '').strip()
    material_number  = (data.get('material_number') or '').strip()
    incoming_quantity = int(data.get('incoming_quantity') or data.get('quantity') or 0)

    if not racking_number:
        return jsonify({'error': 'Racking number is required'}), 400

    # Special bays that skip capacity checks
    special_bays = {"Disposal", "Damage", "Tinter", "Floor", "Chrome Room"}
    if racking_number in special_bays:
        return jsonify({
            'allowed': True,
            'current_quantity': 0,
            'current_space_units': 0,
            'max_capacity_units': None,
            'total_after_units': 0,
            'message': f'Special bay: {racking_number} - no capacity limit'
        }), 200

    # Verify racking exists
    racking = Racking.query.filter_by(racking_number=racking_number).first()
    if not racking:
        return jsonify({'error': f'Invalid racking number: {racking_number}'}), 400

    # Get current items in the rack
    current_items = Stock.query.filter_by(racking_number=racking_number).all()
    
    # Calculate current space units used
    current_space_units = 0
    for stock in current_items:
        sku = SKU.query.get(stock.sku_id)
        if sku and sku.pack_size:
            space_per_item = getattr(sku.pack_size, 'space_units', 1.0)
            current_space_units += stock.quantity * space_per_item
    
    # Get incoming SKU's space units
    incoming_space_per_item = 1.0  # default
    incoming_sku = SKU.query.filter_by(material_number=material_number).first()
    if incoming_sku and incoming_sku.pack_size:
        incoming_space_per_item = getattr(incoming_sku.pack_size, 'space_units', 1.0)
    
    incoming_space_units = incoming_quantity * incoming_space_per_item
    
    # Determine max capacity in space units
    max_capacity_units = None
    warning_msg = None
    
    if current_items or incoming_sku:
        # Collect all relevant pack sizes
        pack_sizes_in_use = set()
        
        for stock in current_items:
            sku = SKU.query.get(stock.sku_id)
            if sku and sku.pack_size:
                pack_sizes_in_use.add(sku.pack_size.id)
        
        if incoming_sku and incoming_sku.pack_size:
            pack_sizes_in_use.add(incoming_sku.pack_size.id)
        
        if pack_sizes_in_use:
            # Get the max_capacity from pack sizes (in space units)
            capacities = []
            for ps_id in pack_sizes_in_use:
                ps = PackSize.query.get(ps_id)
                if ps and ps.max_capacity:
                    # Convert max_capacity (in items) to space units
                    # max_capacity represents how many of THIS pack size fit
                    # So max space units = max_capacity * space_units_per_item
                    capacity_in_space_units = ps.max_capacity * getattr(ps, 'space_units', 1.0)
                    capacities.append(capacity_in_space_units)
            
            if capacities:
                # Use the maximum capacity (most generous)
                # This allows mixing pack sizes up to the largest pallet capacity
                max_capacity_units = max(capacities)
            else:
                warning_msg = f'No capacity configured for pack sizes in {racking_number}'
    
    total_after_units = current_space_units + incoming_space_units
    
    # Check if allowed
    if max_capacity_units is not None:
        allowed = total_after_units <= max_capacity_units
    else:
        # No capacity limit configured - allow with warning
        allowed = True
        if not warning_msg:
            warning_msg = f'Capacity check skipped for {racking_number} - no pack size configured'
    
    # Prepare response
    payload = {
        'allowed': allowed,
        'current_quantity': sum(s.quantity for s in current_items),
        'current_space_units': round(current_space_units, 2),
        'incoming_space_units': round(incoming_space_units, 2),
        'max_capacity_units': round(max_capacity_units, 2) if max_capacity_units else None,
        'total_after_units': round(total_after_units, 2),
        'utilization_percent': round((total_after_units / max_capacity_units * 100), 1) if max_capacity_units else None
    }
    
    # Add warning/error messages
    if warning_msg:
        payload['warning'] = warning_msg
    
    if not allowed and max_capacity_units:
        payload['warning'] = (
            f'Capacity exceeded for {racking_number}. '
            f'Max: {max_capacity_units:.1f} space units, '
            f'Current: {current_space_units:.1f}, '
            f'Incoming: {incoming_space_units:.1f}, '
            f'Total would be: {total_after_units:.1f}'
        )
    
    return jsonify(payload), 200


# Helper function to calculate space units for a stock record
def calculate_space_units(stock):
    """Calculate total space units for a stock record"""
    sku = SKU.query.get(stock.sku_id)
    if not sku or not sku.pack_size:
        return stock.quantity * 1.0  # default 1.0 per item
    
    space_per_item = getattr(sku.pack_size, 'space_units', 1.0)
    return stock.quantity * space_per_item
