# Database Migration Script
# Add space_units field to pack_sizes table

"""
Run this script to add the space_units column to your existing pack_sizes table.

Usage:
    python migration_add_space_units.py
"""

from __init__ import create_app, db
from sqlalchemy import text

def add_space_units_column():
    app = create_app()
    
    with app.app_context():
        try:
            # Add the space_units column with default value 1.0
            with db.engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE pack_sizes ADD COLUMN space_units REAL NOT NULL DEFAULT 1.0"
                ))
                conn.commit()
            
            print("✅ Successfully added space_units column to pack_sizes table")
            print("📝 Default value set to 1.0 for all existing pack sizes")
            print("")
            print("Next steps:")
            print("1. Update space_units values for your pack sizes")
            print("2. Run: python update_space_units.py")
            
        except Exception as e:
            if "duplicate column name" in str(e).lower():
                print("⚠️  Column space_units already exists in pack_sizes table")
            else:
                print(f"❌ Error adding column: {e}")
                raise

if __name__ == "__main__":
    add_space_units_column()
