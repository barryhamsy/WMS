# models.py
from __init__ import db
from datetime import datetime, date
from flask_login import UserMixin
import pytz

# Define your local time zone (GMT+8)
local_tz = pytz.timezone('Asia/Kuala_Lumpur')

def get_local_time():
    # Get the current time in the GMT+8 timezone
    return datetime.now(local_tz)

class SKU(db.Model):
    __tablename__ = 'sku_list'

    id = db.Column(db.Integer, primary_key=True)
    material_number = db.Column(db.String(50), unique=True, nullable=False)
    product_description = db.Column(db.String(200), nullable=False)
    pack_size_id = db.Column(db.Integer, db.ForeignKey('pack_sizes.id'), nullable=False)
    weight = db.Column(db.Float, nullable=False)

    pack_size = db.relationship('PackSize', backref='skus', lazy=True)  # Establish relationship

    def __repr__(self):
        return f"<SKU {self.material_number}: {self.product_description}, Pack Size: {self.pack_size.size},  Weight: {self.weight}>"


class Stock(db.Model):
    __tablename__ = 'stock'

    id = db.Column(db.Integer, primary_key=True)
    sku_id = db.Column(db.Integer, db.ForeignKey('sku_list.id'), nullable=False)  # Foreign key reference to SKU
    sku = db.relationship('SKU', backref='stocks', lazy=True)  # Add relationship to SKU
    quantity = db.Column(db.Integer, default=0, nullable=False)
    batch_number = db.Column(db.String(100), nullable=True)
    shipment_number = db.Column(db.String(100), nullable=True)
    racking_number = db.Column(db.String(100), nullable=True)
    remarks = db.Column(db.String(255), nullable=True)
    timestamp = db.Column(db.DateTime, default=get_local_time)  # New timestamp column

    def __repr__(self):
        return f"<Stock SKU {self.sku_id}: {self.quantity}>"


class StockHistory(db.Model):
    __tablename__ = 'stock_history'

    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stock.id'), nullable=False)
    stock = db.relationship('Stock', backref=db.backref('history', lazy=True))
    change_type = db.Column(db.String(50), nullable=False)  # "GR" or "GI"
    quantity = db.Column(db.Integer, nullable=False)
    username = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=get_local_time)  # Track when the change was made
    remarks = db.Column(db.String(255), nullable=True)

    def __repr__(self):
        return f"<StockHistory {self.change_type} {self.quantity} on stock {self.stock_id} by {self.username}>"


# Define the Racking model
class Racking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    racking_number = db.Column(db.String(20), unique=True, nullable=False)
    aisle = db.Column(db.String(5), nullable=False)
    level = db.Column(db.Integer, nullable=False)
    position = db.Column(db.Integer, nullable=False)
    location = db.Column(db.String(50), nullable=False)

    def __repr__(self):
        return f"<Racking {self.racking_number}>"

class PackSize(db.Model):
    __tablename__ = 'pack_sizes'

    id = db.Column(db.Integer, primary_key=True)
    size = db.Column(db.String(50), unique=True, nullable=False)
    max_capacity = db.Column(db.Integer, nullable=False)  # Keep for backward compatibility
    space_units = db.Column(db.Float, nullable=False, default=1.0)  # NEW FIELD
    
    # space_units represents how much physical space this pack size occupies
    # Examples:
    #   1L bottle = 0.25 space units
    #   5L tin = 1.0 space units (baseline)
    #   20L pail = 4.0 space units
    #   Drum (200L) = 40.0 space units

    def __repr__(self):
        return f"<PackSize {self.size}: capacity={self.max_capacity}, space_units={self.space_units}>"



class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)  # Store hashed passwords
    date_created = db.Column(db.DateTime, default=get_local_time)
    last_login = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)  # For account activation/deactivation
    is_approved = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)

    def get_id(self):
        return self.id  # Return the user's ID

    def __repr__(self):
        return f'<User {self.username}>'

class Order(db.Model):
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)
    dn_number = db.Column(db.String(50), unique=True, nullable=False)  # Add DN number field
    created_at = db.Column(db.DateTime, default=get_local_time)
    customer_name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    sales_order = db.Column(db.String(255), nullable=False)
    remarks = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='active')
    created_by = db.Column(db.String(255), nullable=True)
    last_updated_by = db.Column(db.String(255), nullable=True)  # Add this line
    # Relationship with OrderItem
    items = db.relationship('OrderItem', backref='order', cascade='all, delete-orphan', lazy=True)

    def __repr__(self):
        return f"<Order {self.dn_number} for {self.customer_name}>"

class OrderItem(db.Model):
    __tablename__ = 'order_items'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    sku_id = db.Column(db.Integer, db.ForeignKey('sku_list.id'), nullable=False)
    batch_number = db.Column(db.String(50), nullable=False)  # Add batch number column
    shipment_number = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    racking_number = db.Column(db.String(50), nullable=False)

    # Establish a relationship with SKU
    sku = db.relationship('SKU', backref='order_items')

    def __repr__(self):
        return f"<OrderItem {self.sku_id} with quantity {self.quantity}, batch {self.batch_number}>"

class DailyRackCount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False, default=date.today)
    occupied_racks = db.Column(db.Integer, nullable=False)

    def __repr__(self):
        return f'<DailyRackCount {self.date}: {self.occupied_racks}>'