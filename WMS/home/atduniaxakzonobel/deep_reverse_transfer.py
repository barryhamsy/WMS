"""
DEEP Bulk Transfer Reversal Tool (v2)
======================================
Handles the case where stock was ISSUED (GI) from the wrong rack
after an accidental bulk transfer.

Workflow per skipped entry:
  1. Detect shortfall at destination (qty issued via GI after transfer)
  2. Reverse those GI entries  -> stock returns to destination rack
  3. Reverse the bulk transfer -> stock returns to ORIGINAL rack

USAGE:
  1. STOP the WMS server!
  2. BACKUP: copy warehouse.db warehouse_backup.db
  3. Run:  python deep_reverse_transfer.py
  4. Pick the session, confirm
  5. Restart server
"""

import sqlite3
import re
from datetime import datetime
from collections import defaultdict

DB_PATH = "warehouse.db"

REMARK_RE = re.compile(r'Bulk transferred (\d+) units from (\S+) to (\S+)')


def get_sessions(conn, include_reversed=False):
    """Group bulk_transfer history into sessions by destination + 5-min window."""
    cur = conn.cursor()
    reversed_filter = "" if include_reversed else "AND remarks NOT LIKE '%[REVERSED]%'"
    cur.execute(f"""
        SELECT id, stock_id, quantity, username, timestamp, remarks
        FROM stock_history
        WHERE change_type = 'bulk_transfer'
          AND remarks LIKE 'Bulk transferred%'
          {reversed_filter}
        ORDER BY timestamp DESC
        LIMIT 500
    """)
    rows = cur.fetchall()

    sessions = []
    current = None

    for row in rows:
        hist_id, stock_id, qty, username, ts, remarks = row
        m = REMARK_RE.search(remarks or '')
        if not m:
            continue
        moved_qty, source, dest = int(m.group(1)), m.group(2), m.group(3)
        ts_dt = datetime.fromisoformat(ts)

        if (current is None
                or current['dest'] != dest
                or abs((current['last_ts'] - ts_dt).total_seconds()) > 300):
            current = {
                'dest': dest, 'first_ts': ts_dt, 'last_ts': ts_dt,
                'username': username, 'entries': []
            }
            sessions.append(current)

        current['last_ts'] = ts_dt
        current['entries'].append({
            'hist_id': hist_id, 'stock_id': stock_id, 'qty': moved_qty,
            'source': source, 'dest': dest, 'ts': ts,
        })

    return sessions


def find_dest_stock(cur, sku_id, batch, shipment, dest):
    cur.execute("""
        SELECT id, quantity FROM stock
        WHERE sku_id = ? AND racking_number = ?
          AND IFNULL(batch_number,'') = IFNULL(?, '')
          AND IFNULL(shipment_number,'') = IFNULL(?, '')
    """, (sku_id, dest, batch, shipment))
    return cur.fetchone()


def reverse_gi_for_stock(cur, dest_stock_id, needed_qty, after_ts):
    """Reverse GI entries on a stock record (newest first) until needed_qty recovered.
       Returns total qty recovered."""
    cur.execute("""
        SELECT id, change_type, quantity, timestamp, IFNULL(remarks,'')
        FROM stock_history
        WHERE stock_id = ?
          AND timestamp > ?
          AND (change_type = 'GI' OR change_type LIKE 'GI - picked%')
          AND IFNULL(remarks,'') NOT LIKE '%[GI-REVERSED]%'
        ORDER BY timestamp DESC
    """, (dest_stock_id, after_ts))
    gi_rows = cur.fetchall()

    recovered = 0
    for gi_id, ctype, gi_qty, gi_ts, gi_remarks in gi_rows:
        if recovered >= needed_qty:
            break
        gi_qty = abs(int(gi_qty))
        take = min(gi_qty, needed_qty - recovered)

        # Add the issued quantity back to destination stock
        cur.execute("UPDATE stock SET quantity = quantity + ? WHERE id = ?", (take, dest_stock_id))

        # Log GI reversal
        now = datetime.now().isoformat(sep=' ')
        cur.execute("""
            INSERT INTO stock_history (stock_id, change_type, quantity, username, timestamp, remarks)
            VALUES (?, 'GI_reversal', ?, 'SYSTEM-REVERSAL', ?, ?)
        """, (dest_stock_id, take, now,
              f"[GI-REVERSED] Returned {take} units (reversing '{ctype}' from {gi_ts[:19]})"))

        # Mark the GI entry as reversed
        cur.execute("UPDATE stock_history SET remarks = IFNULL(remarks,'') || ' [GI-REVERSED]' WHERE id = ?", (gi_id,))

        recovered += take

    return recovered


def deep_reverse_session(conn, session):
    cur = conn.cursor()
    reversed_count = 0
    gi_reversed_total = 0
    errors = []

    for e in session['entries']:
        stock_id, qty, source, dest = e['stock_id'], e['qty'], e['source'], e['dest']

        cur.execute("SELECT id, sku_id, batch_number, shipment_number, racking_number FROM stock WHERE id = ?", (stock_id,))
        src_row = cur.fetchone()
        if not src_row:
            errors.append(f"Stock ID {stock_id} not found - skipped")
            continue
        _, sku_id, batch, shipment, src_rack_now = src_row

        dest_row = find_dest_stock(cur, sku_id, batch, shipment, dest)
        if not dest_row:
            errors.append(f"Dest stock not found for stock_id={stock_id} at {dest} - skipped")
            continue
        dest_id, dest_qty = dest_row

        # ── STEP 1: If shortfall, reverse GI entries first ──────────────
        if dest_qty < qty:
            shortfall = qty - dest_qty
            recovered = reverse_gi_for_stock(cur, dest_id, shortfall, e['ts'])
            gi_reversed_total += recovered
            dest_qty += recovered

            if dest_qty < qty:
                errors.append(
                    f"stock_id={stock_id}: still short after GI reversal "
                    f"(have {dest_qty}, need {qty}). Reversed partially: {dest_qty} units."
                )
                qty = dest_qty   # reverse what we can
                if qty <= 0:
                    continue

        # ── STEP 2: Reverse the bulk transfer ───────────────────────────
        cur.execute("UPDATE stock SET quantity = quantity - ? WHERE id = ?", (qty, dest_id))
        cur.execute("UPDATE stock SET quantity = quantity + ? WHERE id = ?", (qty, stock_id))
        if src_rack_now != source:
            cur.execute("UPDATE stock SET racking_number = ? WHERE id = ?", (source, stock_id))

        now = datetime.now().isoformat(sep=' ')
        cur.execute("""
            INSERT INTO stock_history (stock_id, change_type, quantity, username, timestamp, remarks)
            VALUES (?, 'bulk_transfer_reversal', ?, 'SYSTEM-REVERSAL', ?, ?)
        """, (stock_id, qty, now, f"[REVERSED] Returned {qty} units from {dest} back to {source}"))

        cur.execute("UPDATE stock_history SET remarks = remarks || ' [REVERSED]' WHERE id = ?", (e['hist_id'],))
        reversed_count += 1

    # Clean up zero-qty rows at destination
    cur.execute("DELETE FROM stock WHERE quantity <= 0 AND racking_number = ?", (session['dest'],))
    conn.commit()
    return reversed_count, gi_reversed_total, errors


def main():
    conn = sqlite3.connect(DB_PATH)
    sessions = get_sessions(conn)

    if not sessions:
        print("No reversible bulk transfer sessions found.")
        return

    print("\n=== Bulk Transfer Sessions (not yet reversed) ===\n")
    for i, s in enumerate(sessions[:15]):
        total_qty = sum(e['qty'] for e in s['entries'])
        sources = sorted({e['source'] for e in s['entries']})
        print(f"[{i}] {s['first_ts'].strftime('%Y-%m-%d %H:%M')}  →  {s['dest']}")
        print(f"     {len(s['entries'])} transfers, {total_qty} units, by {s['username']}")
        print(f"     Sources: {', '.join(sources[:8])}{'...' if len(sources) > 8 else ''}\n")

    choice = input("Enter session number to DEEP REVERSE (or 'q' to quit): ").strip()
    if choice.lower() == 'q':
        return
    try:
        session = sessions[int(choice)]
    except (ValueError, IndexError):
        print("Invalid choice.")
        return

    total_qty = sum(e['qty'] for e in session['entries'])
    print(f"\nDeep reversal will:")
    print(f"  1. Reverse any GI issuances that consumed transferred stock at {session['dest']}")
    print(f"  2. Return {total_qty} units to their original racks")
    confirm = input("Type 'YES' to confirm: ").strip()
    if confirm != 'YES':
        print("Cancelled.")
        return

    reversed_count, gi_total, errors = deep_reverse_session(conn, session)

    print(f"\n[OK] Reversed {reversed_count} transfers")
    print(f"[OK] Recovered {gi_total} units from GI reversals")
    if errors:
        print(f"\n[!!] {len(errors)} issues:")
        for err in errors:
            print(f"   - {err}")

    conn.close()
    print("\nDone! Restart WMS and verify in Stock List.")
    print("NOTE: Reversed GI means those order picks are back in stock —")
    print("      re-pick those orders from the correct racks if needed.")


if __name__ == "__main__":
    main()
