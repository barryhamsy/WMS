# __init__.py
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from config import (Config)

db = SQLAlchemy()

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    # Automatically create all tables when the app starts
    with app.app_context():
        db.create_all()  # This creates all tables

    return app

