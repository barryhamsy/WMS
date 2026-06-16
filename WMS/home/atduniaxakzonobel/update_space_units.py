# Update Space Units for Pack Sizes
# This script sets appropriate space_units values based on pack size volume

"""
This script helps you set space_units values for your pack sizes.
Space units represent relative physical space occupied.

PREREQUISITE: Run migration_add_space_units.py first!

Usage:
    python update_space_units.py
"""

from __init__ import create_app, db
from models import PackSize
from sqlalchemy import inspect

# Define space unit mappings
# Use 5L tin as baseline (1.0 space unit)
SPACE_UNIT_MAPPINGS = {
    # Small containers
    '1': 0.25,
    '1.3': 0.25,
    '1.43': 0.25,
    
    # Medium containers (baseline)
    '5': 0.125,
    '3': 0.125,
    
    # Large containers
    '10': 2.0,
    '20': 3.0,
    '25': 4.0,

    # Extra large
    '50L': 10.0,
    '100L': 20.0,
    '200L': 40.0,
    'Drum': 40.0,
    'IBC': 200.0,  # Intermediate Bulk Container (1000L)
    
    # Boxes/cartons (estimate based on typical sizes)
    'Small Box': 0.5,
    'Medium Box': 1.0,
    'Large Box': 2.0,
    'Carton': 1.5,
    
    # Pallets
    'Pallet': 100.0,  # Full pallet as reference
}

def check_space_units_column_exists():
    """Check if space_units column exists in pack_sizes table"""
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('pack_sizes')]
        return 'space_units' in columns

def update_space_units():
    app = create_app()
    
    with app.app_context():
        # First, check if space_units column exists
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('pack_sizes')]
        
        if 'space_units' not in columns:
            print("❌ ERROR: space_units column does not exist in pack_sizes table!")
            print("")
            print("Please run the migration first:")
            print(">>> python migration_add_space_units.py")
            print("")
            return
        
        pack_sizes = PackSize.query.all()
        
        if not pack_sizes:
            print("⚠️  No pack sizes found in database")
            return
        
        print("🔍 Found pack sizes:")
        print("-" * 60)
        
        updated_count = 0
        manual_review = []
        
        for ps in pack_sizes:
            # Get current space_units value (should default to 1.0 from migration)
            current_value = ps.space_units if hasattr(ps, 'space_units') else 1.0
            
            # Try to find matching space unit
            matched = False
            for key, space_unit in SPACE_UNIT_MAPPINGS.items():
                if key.lower() in ps.size.lower():
                    ps.space_units = space_unit
                    matched = True
                    print(f"✅ {ps.size:20} → {space_unit:6.2f} space units (was {current_value:.2f})")
                    updated_count += 1
                    break
            
            if not matched:
                manual_review.append(ps)
                print(f"⚠️  {ps.size:20} → {current_value:6.2f} space units (no auto-match, keeping current)")
        
        # Commit changes
        db.session.commit()
        
        print("-" * 60)
        print(f"✅ Updated {updated_count} pack sizes")
        
        if manual_review:
            print("")
            print("⚠️  Manual review needed for:")
            for ps in manual_review:
                current = ps.space_units if hasattr(ps, 'space_units') else 1.0
                print(f"   - {ps.size} (currently {current} space units)")
            print("")
            print("To update manually, use Python shell:")
            print(">>> from models import PackSize")
            print(">>> from __init__ import db")
            print(">>> ps = PackSize.query.filter_by(size='YourPackSize').first()")
            print(">>> ps.space_units = 2.5  # Set your value")
            print(">>> db.session.commit()")

if __name__ == "__main__":
    print("=" * 60)
    print("   Pack Size Space Units Update")
    print("=" * 60)
    print("")
    
    # Check if column exists first
    if not check_space_units_column_exists():
        print("❌ ERROR: space_units column not found!")
        print("")
        print("You must run the migration first:")
        print("")
        print("Step 1: python migration_add_space_units.py")
        print("Step 2: Update models.py to include space_units field")
        print("Step 3: python update_space_units.py (this script)")
        print("")
    else:
        update_space_units()
        print("")
        print("✅ Update complete!")
