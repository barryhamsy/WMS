# Updated PackSize Model with Space Units

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
