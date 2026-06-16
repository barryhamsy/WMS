# Managing Mixed Pack Sizes on Pallets - Complete Guide

## The Problem

Your warehouse needs to store different pack sizes together on the same pallet in the same racking location.

### Current System Limitation
- **Current:** Capacity = number of items (e.g., max 100 items)
- **Problem:** Doesn't account for size differences
  - 1 × 20L pail ≠ 1 × 1L bottle (in physical space)
  - Can't accurately manage mixed pallets

## The Solution: Space Units System

### Concept
Instead of counting **items**, we measure **space occupied**.

**Space Units** = A standardized measure of physical space
- 1 space unit = volume occupied by 1 × 5L tin (baseline)
- Smaller items = fraction of 1 unit
- Larger items = multiple units

### How It Works

#### 1. Define Space Units for Each Pack Size

| Pack Size | Space Units | Explanation |
|-----------|-------------|-------------|
| 1L bottle | 0.25 | Takes 1/4 space of 5L tin |
| 5L tin | 1.0 | Baseline reference |
| 20L pail | 4.0 | Takes 4x space of 5L tin |
| 200L drum | 40.0 | Takes 40x space of 5L tin |

#### 2. Calculate Total Space Used

**Formula:**
```
Total Space Units = Σ (Quantity × Space Units per Item)
```

#### 3. Check Against Rack Capacity

**Rack Capacity** = Maximum space units allowed (e.g., 100 space units)

---

## Your Examples - Solved!

### Example 1: Pallet 1 - Mixed 20L + 5L

**Contents:**
- 20 pails × 20L pack size
- 16 tins × 5L pack size

**Space Calculation:**

| Item | Quantity | Space Units/Item | Total Space Units |
|------|----------|------------------|-------------------|
| 20L pails | 20 | 4.0 | 80.0 |
| 5L tins | 16 | 1.0 | 16.0 |
| **TOTAL** | | | **96.0** |

**Result:**
- ✅ Total = 96.0 space units
- ✅ Fits in rack with 100 space unit capacity
- ✅ System allows this combination

### Example 2: Pallet 2 - Mixed 1L + 5L

**Contents:**
- 200 pieces × 1L pack size (= 20 boxes)
- 40 tins × 5L pack size

**Space Calculation:**

| Item | Quantity | Space Units/Item | Total Space Units |
|------|----------|------------------|-------------------|
| 1L bottles | 200 | 0.25 | 50.0 |
| 5L tins | 40 | 1.0 | 40.0 |
| **TOTAL** | | | **90.0** |

**Result:**
- ✅ Total = 90.0 space units
- ✅ Fits in rack with 100 space unit capacity
- ✅ System allows this combination

### Example 3: Would NOT Fit

**Contents:**
- 30 pails × 20L pack size
- 20 tins × 5L pack size

**Space Calculation:**

| Item | Quantity | Space Units/Item | Total Space Units |
|------|----------|------------------|-------------------|
| 20L pails | 30 | 4.0 | 120.0 |
| 5L tins | 20 | 1.0 | 20.0 |
| **TOTAL** | | | **140.0** |

**Result:**
- ❌ Total = 140.0 space units
- ❌ Exceeds rack capacity (100 space units)
- ❌ System blocks this - capacity exceeded

---

## Implementation Steps

### Step 1: Add Space Units Column to Database

Run the migration script:
```bash
python migration_add_space_units.py
```

This adds `space_units` column to `pack_sizes` table.

### Step 2: Set Space Units for Your Pack Sizes

Run the update script:
```bash
python update_space_units.py
```

Or manually set values:
```python
from models import PackSize
from __init__ import db

# Update 1L pack size
ps_1l = PackSize.query.filter_by(size='1L').first()
ps_1l.space_units = 0.25
db.session.commit()

# Update 5L pack size
ps_5l = PackSize.query.filter_by(size='5L').first()
ps_5l.space_units = 1.0
db.session.commit()

# Update 20L pack size
ps_20l = PackSize.query.filter_by(size='20L').first()
ps_20l.space_units = 4.0
db.session.commit()
```

### Step 3: Update Rack Capacities

Your rack's `max_capacity` now means **"maximum space units"**, not item count.

**Example:**
- Old: Rack A-01 can hold "100 items"
- New: Rack A-01 can hold "100 space units"
  - = 100 × 5L tins
  - = 25 × 20L pails
  - = 400 × 1L bottles
  - = ANY mix totaling 100 space units

### Step 4: Update Models.py

Add the `space_units` field to PackSize model:
```python
class PackSize(db.Model):
    __tablename__ = 'pack_sizes'
    
    id = db.Column(db.Integer, primary_key=True)
    size = db.Column(db.String(50), unique=True, nullable=False)
    max_capacity = db.Column(db.Integer, nullable=False)
    space_units = db.Column(db.Float, nullable=False, default=1.0)  # NEW
    
    def __repr__(self):
        return f"<PackSize {self.size}: {self.max_capacity}, {self.space_units} units>"
```

### Step 5: Update Capacity Check in app.py

Replace the `check-racking-capacity` function with the new version that calculates space units.

---

## How to Determine Space Units

### Method 1: Based on Volume Ratio

Use actual container volumes:
```
Space Units = Container Volume / Baseline Volume

Baseline = 5L
1L bottle = 1 / 5 = 0.2 ≈ 0.25
20L pail = 20 / 5 = 4.0
```

### Method 2: Based on Physical Dimensions

Measure actual space occupied:
```
Space Units = (Length × Width × Height) / Baseline Volume

Example:
20L pail: 30cm × 30cm × 40cm = 36,000 cm³
5L tin: 20cm × 20cm × 25cm = 10,000 cm³
Ratio = 36,000 / 10,000 = 3.6 ≈ 4.0
```

### Method 3: Based on Pallet Loading

Count how many fit on a standard pallet:
```
Standard pallet = 100 space units (reference)

If pallet holds:
- 400 × 1L bottles → 1L = 100/400 = 0.25 units
- 100 × 5L tins → 5L = 100/100 = 1.0 units
- 25 × 20L pails → 20L = 100/25 = 4.0 units
```

---

## Recommended Space Unit Values

Based on common paint/chemical containers:

| Pack Size | Space Units | Notes |
|-----------|-------------|-------|
| **Small Containers** |
| 250ml | 0.06 | Very small cans |
| 500ml | 0.12 | Small tins |
| 1L | 0.25 | Standard small tin |
| **Medium Containers** |
| 2L | 0.5 | Medium tin |
| 4L | 0.8 | Large tin |
| 5L | 1.0 | **Baseline reference** |
| **Large Containers** |
| 10L | 2.0 | Small pail |
| 15L | 3.0 | Medium pail |
| 20L | 4.0 | Standard pail |
| **Extra Large** |
| 50L | 10.0 | Drum |
| 100L | 20.0 | Large drum |
| 200L | 40.0 | Standard drum |
| **Bulk** |
| IBC (1000L) | 200.0 | Intermediate Bulk Container |

---

## Real-World Scenarios

### Scenario 1: Paint Warehouse

**Rack Capacity:** 120 space units

**Allowed Combinations:**

```
Option A: Single pack size
- 120 × 5L tins = 120 units ✅

Option B: Mixed small + medium
- 200 × 1L (50 units) + 70 × 5L (70 units) = 120 units ✅

Option C: Mixed large + small
- 20 × 20L pails (80 units) + 160 × 1L (40 units) = 120 units ✅

Option D: All three sizes
- 10 × 20L (40 units) + 40 × 5L (40 units) + 160 × 1L (40 units) = 120 units ✅
```

**Blocked Combination:**

```
- 35 × 20L pails = 140 units ❌ Exceeds capacity
```

### Scenario 2: Your Actual Use Case

**Rack Capacity:** 100 space units

**Pallet 1:**
- 20 × 20L pails = 80 units
- 16 × 5L tins = 16 units
- **Total = 96 units** ✅ Allowed

**Pallet 2:**
- 200 × 1L bottles = 50 units
- 40 × 5L tins = 40 units
- **Total = 90 units** ✅ Allowed

---

## System Response Examples

### When GR is Allowed

```json
{
  "allowed": true,
  "current_quantity": 36,
  "current_space_units": 96.0,
  "incoming_space_units": 0,
  "max_capacity_units": 100.0,
  "total_after_units": 96.0,
  "utilization_percent": 96.0,
  "message": "✅ Sufficient space available"
}
```

### When Capacity Exceeded

```json
{
  "allowed": false,
  "current_space_units": 80.0,
  "incoming_space_units": 40.0,
  "max_capacity_units": 100.0,
  "total_after_units": 120.0,
  "utilization_percent": 120.0,
  "warning": "Capacity exceeded for A-01-L1. Max: 100.0 space units, Current: 80.0, Incoming: 40.0, Total would be: 120.0"
}
```

---

## UI Enhancement Suggestions

### Show Space Units in Stock View

```
┌─────────────────────────────────────────────────────┐
│ Racking: A-01-L1                                   │
├─────────────────────────────────────────────────────┤
│ Material  │ Pack Size │ Qty │ Space Units         │
├───────────┼───────────┼─────┼─────────────────────┤
│ MAT-001   │ 20L       │ 20  │ 80.0                │
│ MAT-002   │ 5L        │ 16  │ 16.0                │
├───────────┴───────────┴─────┼─────────────────────┤
│ Total Items: 36             │ Total: 96.0 / 100.0 │
│                             │ (96% utilized)      │
└─────────────────────────────┴─────────────────────┘
```

### GR Validation Message

```
Adding 10 × 20L pails to rack A-01-L1:

Current usage: 96.0 / 100.0 space units (96%)
Incoming: 40.0 space units
After GR: 136.0 / 100.0 space units (136%)

❌ Capacity exceeded by 36.0 space units
   Please select different rack or reduce quantity
```

---

## Benefits

### ✅ Accurate Capacity Management
- Accounts for physical size differences
- Prevents overfilling racks
- Maximizes space utilization

### ✅ Flexible Pallet Mixing
- Mix different pack sizes freely
- System validates automatically
- No manual calculations needed

### ✅ Better Planning
- See exact space available
- Plan loading efficiently
- Optimize warehouse layout

### ✅ Safety & Compliance
- Prevents overloading
- Maintains aisle clearances
- Ensures stability

---

## Migration Checklist

- [ ] Backup current database
- [ ] Run migration to add `space_units` column
- [ ] Update `models.py` with new field
- [ ] Set space units for all pack sizes
- [ ] Update capacity check logic in `app.py`
- [ ] Test with your actual pack sizes
- [ ] Review and adjust rack capacities if needed
- [ ] Train staff on new system
- [ ] Update documentation

---

## FAQ

### Q: What if I don't know exact dimensions?

**A:** Use volume ratios as approximation. Start with standard values and adjust based on experience.

### Q: Can I change space units later?

**A:** Yes! Update the `space_units` value in PackSize table. Affects future calculations immediately.

### Q: What about irregularly shaped items?

**A:** Use the maximum dimensions (length × width × height) to calculate worst-case space needed.

### Q: Should every rack have the same capacity?

**A:** No! Different rack types can have different capacities:
- Floor racks: 150 space units (larger capacity)
- Wall racks: 80 space units (smaller)
- High racks: 200 space units (extra tall)

### Q: What about empty space between items?

**A:** Space units should include typical stacking gaps. For example:
- Actual 20L pail volume: 20L
- Space needed with gaps: ~25L equivalent
- Use 4.0 space units (assuming 5L = 1.0)

---

## Summary

**Before (Item Count):**
- ❌ Can't mix pack sizes accurately
- ❌ Either too restrictive or too permissive
- ❌ Manual calculations needed

**After (Space Units):**
- ✅ Mix any pack sizes on same pallet
- ✅ Automatic accurate capacity checking
- ✅ Maximum space utilization
- ✅ Better warehouse management

**Your Examples Work:**
- ✅ 20×20L + 16×5L = 96 units (allowed)
- ✅ 200×1L + 40×5L = 90 units (allowed)

---

**Implementation Time:** 1-2 hours  
**Complexity:** Medium  
**Impact:** High - Enables flexible mixed pallet management  
**Backward Compatible:** Yes (defaults to 1.0 if not set)  
