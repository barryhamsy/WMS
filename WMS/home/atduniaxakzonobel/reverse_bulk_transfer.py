"""
Bulk Transfer Reversal Tool
============================
Reverses accidental bulk transfers using StockHistory records.

USAGE:
  1. STOP the WMS server first!
  2. Place this script next to your warehouse.db
  3. Run:  python reverse_bulk_transfer.py
  4. It shows recent transfer sessions - pick the one to reverse
  5. Restart WMS server

The script:
  - Parses 'Bulk transferred X units from SOURCE to DEST' remarks
  - Moves quantity back from DEST to the original SOURCE stock record
  - Logs reversal entries in stock_history
"""

import sqlite3
import re
import sys
from datetime import datetime
from collections import defaultdict

DB_PATH = "warehouse.db"   # adjust if needed

REMARK_RE = re.compile(
    r'Bulk transferred (\d+) units from (\S+) to (\S+)'
)


def get_sessions(conn):
    """Group bulk_transfer history into sessions by destination + time window (5 min)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, stock_id, quantity, username, timestamp, remarks
        FROM stock_history
        WHERE change_type = 'bulk_transfer'
          AND remarks LIKE 'Bulk transferred%'
          AND remarks NOT LIKE '%[REVERSED]%'
        ORDER BY timestamp DESC
        LIMIT 500
    """)
    rows = cur.fetchall()

    sessions = []          # list of dicts
    current = None

    for row in rows:
        hist_id, stock_id, qty, username, ts, remarks = row
        m = REMARK_RE.search(remarks or '')
        if not m:
            continue
        moved_qty, source, dest = int(m.group(1)), m.group(2), m.group(3)
        ts_dt = datetime.fromisoformat(ts)

        # New session if destination differs or gap > 5 minutes
        if (current is None
                or current['dest'] != dest
                or abs((current['last_ts'] - ts_dt).total_seconds()) > 300):
            current = {
                'dest': dest,
                'first_ts': ts_dt,
                'last_ts': ts_dt,
                'username': username,
                'entries': []
            }
            sessions.append(current)

        current['last_ts'] = ts_dt
        current['entries'].append({
            'hist_id': hist_id,
            'stock_id': stock_id,
            'qty': moved_qty,
            'source': source,
            'dest': dest,
        })

    return sessions


def reverse_session(conn, session):
    cur = conn.cursor()
    reversed_count = 0
    errors = []

    for e in session['entries']:
        stock_id = e['stock_id']
        qty      = e['qty']
        source   = e['source']
        dest     = e['dest']

        # Original source stock record
        cur.execute("SELECT id, sku_id, batch_number, shipment_number, racking_number, quantity FROM stock WHERE id = ?", (stock_id,))
        src_row = cur.fetchone()
        if not src_row:
            errors.append(f"Stock ID {stock_id} not found - skipped")
            continue

        _, sku_id, batch, shipment, src_rack_now, src_qty_now = src_row

        # Find the destination stock record (same sku/batch/shipment at dest rack)
        cur.execute("""
            SELECT id, quantity FROM stock
            WHERE sku_id = ? AND racking_number = ?
              AND IFNULL(batch_number,'') = IFNULL(?, '')
              AND IFNULL(shipment_number,'') = IFNULL(?, '')
        """, (sku_id, dest, batch, shipment))
        dest_row = cur.fetchone()

        if not dest_row:
            errors.append(f"Dest stock not found for stock_id={stock_id} sku={sku_id} at {dest} - skipped")
            continue

        dest_id, dest_qty = dest_row

        if dest_qty < qty:
            errors.append(f"Dest {dest} only has {dest_qty}, needed {qty} for stock_id={stock_id} - skipped (partial stock may have been issued)")
            continue

        # 1. Reduce destination
        cur.execute("UPDATE stock SET quantity = quantity - ? WHERE id = ?", (qty, dest_id))

        # 2. Add back to original source record
        cur.execute("UPDATE stock SET quantity = quantity + ? WHERE id = ?", (qty, stock_id))

        # If the source record's racking was changed, restore it
        if src_rack_now != source:
            cur.execute("UPDATE stock SET racking_number = ? WHERE id = ?", (source, stock_id))

        # 3. Log reversal
        now = datetime.now().isoformat(sep=' ')
        cur.execute("""
            INSERT INTO stock_history (stock_id, change_type, quantity, username, timestamp, remarks)
            VALUES (?, 'bulk_transfer_reversal', ?, 'SYSTEM-REVERSAL', ?, ?)
        """, (stock_id, qty,
              now,
              f"[REVERSED] Returned {qty} units from {dest} back to {source}"))

        # 4. Mark original history as reversed
        cur.execute("""
            UPDATE stock_history SET remarks = remarks || ' [REVERSED]'
            WHERE id = ?
        """, (e['hist_id'],))

        reversed_count += 1

    # Clean up: delete zero-quantity stock rows at destination
    cur.execute("DELETE FROM stock WHERE quantity <= 0 AND racking_number = ?", (session['dest'],))

    conn.commit()
    return reversed_count, errors


def main():
    conn = sqlite3.connect(DB_PATH)

    sessions = get_sessions(conn)
    if not sessions:
        print("No reversible bulk transfer sessions found.")
        return

    print("\n=== Recent Bulk Transfer Sessions ===\n")
    for i, s in enumerate(sessions[:15]):
        total_qty = sum(e['qty'] for e in s['entries'])
        sources = sorted({e['source'] for e in s['entries']})
        print(f"[{i}] {s['first_ts'].strftime('%Y-%m-%d %H:%M')}  →  {s['dest']}")
        print(f"     {len(s['entries'])} transfers, {total_qty} units total, by {s['username']}")
        print(f"     Sources: {', '.join(sources[:8])}{'...' if len(sources) > 8 else ''}\n")

    choice = input("Enter session number to REVERSE (or 'q' to quit): ").strip()
    if choice.lower() == 'q':
        return

    try:
        session = sessions[int(choice)]
    except (ValueError, IndexError):
        print("Invalid choice.")
        return

    total_qty = sum(e['qty'] for e in session['entries'])
    print(f"\nAbout to reverse {len(session['entries'])} transfers ({total_qty} units)")
    print(f"Destination {session['dest']} → back to original racks")
    confirm = input("Type 'YES' to confirm: ").strip()

    if confirm != 'YES':
        print("Cancelled.")
        return

    reversed_count, errors = reverse_session(conn, session)

    print(f"\n[OK] Reversed {reversed_count} transfers successfully!")
    if errors:
        print(f"\n⚠ {len(errors)} entries could not be reversed:")
        for err in errors:
            print(f"   - {err}")

    conn.close()
    print("\nDone! Restart your WMS server and verify in Stock List.")


if __name__ == "__main__":
    main()
