"""
Daily auto-import: Fetches today's orders from Google Sheets and imports them
into the Culture Circle inventory dashboard, deducting stock and recording
burn rate movements.

Usage:
  python daily_import.py                         # Import from all configured sheets
  python daily_import.py --sheet "March Paid"    # Import from a specific sheet
  python daily_import.py --dry-run               # Preview without writing to DB

Set up as a daily cron job:
  0 9 * * * cd /Users/anshjindal/claude_project/culture_circle_inventory && python daily_import.py >> /tmp/cc_daily_import.log 2>&1
"""

import sys
import os
import hashlib
import argparse
from datetime import datetime

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras
from tools.google_sheets import get_service

# Config
SPREADSHEET_ID = '1jvR4tBYh26CuZZnzY1rOmzMJWtkORd8g3tGYmT4m0Og'
SHEETS_TO_IMPORT = ['Feb', 'March', 'March Paid']
DB_CONFIG = {
    'dbname': os.environ.get('DB_NAME', 'culture_circle_inventory'),
    'user': os.environ.get('DB_USER', 'anshjindal'),
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': os.environ.get('DB_PORT', '5432'),
}


def get_db():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    return conn


def fetch_sheet_data(sheet_name):
    """Fetch all rows from a Google Sheet tab."""
    service = get_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{sheet_name}!A1:F10000"
    ).execute()
    return result.get('values', [])


def find_blank_for_product(cur, product, size):
    """Find blank_id for a product+size via sku_blank_mapping or auto-mapping."""
    # Check existing mapping
    cur.execute(
        "SELECT blank_id FROM sku_blank_mapping WHERE product = %s AND size = %s",
        (product, size)
    )
    row = cur.fetchone()
    if row:
        return row[0]

    # Auto-map via keyword extraction
    from services.auto_mapper import extract_garment_type, extract_color

    garment_type = extract_garment_type(product)
    color = extract_color(product)

    if garment_type and color:
        # Try exact match on garment_type + color + size
        cur.execute(
            """SELECT blank_id FROM blank_master
               WHERE LOWER(garment_type) = LOWER(%s) AND LOWER(color) = LOWER(%s) AND size = %s AND is_active = TRUE""",
            (garment_type, color, size)
        )
        row = cur.fetchone()
        if row:
            # Save mapping for future
            try:
                cur.execute(
                    """INSERT INTO sku_blank_mapping (product, brand, size, blank_id)
                       VALUES (%s, %s, %s, %s) ON CONFLICT (product, size) DO NOTHING""",
                    (product, None, size, row[0])
                )
            except Exception:
                pass
            return row[0]

    if garment_type:
        # Try garment_type + size only
        cur.execute(
            """SELECT blank_id FROM blank_master
               WHERE LOWER(garment_type) = LOWER(%s) AND size = %s AND is_active = TRUE LIMIT 1""",
            (garment_type, size)
        )
        row = cur.fetchone()
        if row:
            return row[0]

    return None


def import_orders(sheet_name, dry_run=False):
    """Import orders from a single sheet."""
    print(f"\n{'='*60}")
    print(f"Importing: {sheet_name}")
    print(f"{'='*60}")

    data = fetch_sheet_data(sheet_name)
    if not data or len(data) < 2:
        print(f"  No data found in {sheet_name}")
        return 0, 0, 0

    headers = data[0]
    print(f"  Headers: {headers}")
    print(f"  Total rows: {len(data) - 1}")

    # Determine column indices
    col_map = {}
    for i, h in enumerate(headers):
        h_lower = h.strip().lower()
        if 'order' in h_lower and 'id' in h_lower:
            col_map['order_id'] = i
        elif 'sourcex' in h_lower or 'sx' in h_lower:
            col_map['sx_id'] = i
        elif 'product' in h_lower:
            col_map['product'] = i
        elif 'size' in h_lower:
            col_map['size'] = i
        elif 'status' in h_lower:
            col_map['status'] = i
        elif 'created' in h_lower or 'date' in h_lower:
            col_map['date'] = i

    if 'order_id' not in col_map or 'product' not in col_map:
        print(f"  ERROR: Could not find Order ID or Product columns")
        return 0, 0, 0

    conn = get_db()
    cur = conn.cursor()

    # Create import batch
    file_hash = hashlib.sha256(f"{sheet_name}_{datetime.now().date()}".encode()).hexdigest()

    if not dry_run:
        # Check for duplicate import today
        cur.execute(
            "SELECT batch_id FROM import_batches WHERE file_hash = %s",
            (file_hash,)
        )
        if cur.fetchone():
            print(f"  Already imported {sheet_name} today. Skipping.")
            conn.close()
            return 0, 0, 0

        cur.execute(
            """INSERT INTO import_batches (file_hash, file_name, order_count)
               VALUES (%s, %s, %s) RETURNING batch_id""",
            (file_hash, f"{sheet_name}_auto_{datetime.now().date()}", len(data) - 1)
        )
        batch_id = cur.fetchone()[0]

    deducted = 0
    unmapped = 0
    skipped = 0

    for row in data[1:]:
        # Pad row
        while len(row) < len(headers):
            row.append('')

        order_id = row[col_map['order_id']].strip()
        product = row[col_map['product']].strip()
        size = row[col_map.get('size', -1)].strip() if col_map.get('size') is not None else ''
        status = row[col_map.get('status', -1)].strip().lower() if col_map.get('status') is not None else 'paid'

        if not order_id or not product:
            continue
        if status == 'cancelled':
            continue

        if not dry_run:
            # Check for duplicate order
            cur.execute("SELECT order_id FROM daily_orders WHERE order_id = %s", (order_id,))
            if cur.fetchone():
                skipped += 1
                continue

        # Find blank
        blank_id = find_blank_for_product(cur, product, size)

        if not dry_run:
            # Insert order
            cur.execute(
                """INSERT INTO daily_orders (order_id, product, size, status, blank_id, import_batch_id)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (order_id) DO NOTHING""",
                (order_id, product, size, status, blank_id, batch_id)
            )

        if blank_id:
            if not dry_run:
                # Deduct stock
                cur.execute(
                    "UPDATE blank_master SET current_stock = current_stock - 1, updated_at = NOW() WHERE blank_id = %s",
                    (blank_id,)
                )
                cur.execute("SELECT current_stock FROM blank_master WHERE blank_id = %s", (blank_id,))
                new_balance = cur.fetchone()[0]
                # Log movement for burn rate tracking
                cur.execute(
                    """INSERT INTO stock_movements (blank_id, movement_type, quantity, balance_after, reference_type, reference_id, notes, movement_date)
                       VALUES (%s, 'CSV_DEDUCTION', -1, %s, 'import_batch', %s, %s, CURRENT_DATE)""",
                    (blank_id, new_balance, batch_id, f"Order {order_id}")
                )
            deducted += 1
        else:
            if not dry_run:
                cur.execute(
                    """INSERT INTO unmapped_sku_log (product, size, import_batch_id)
                       VALUES (%s, %s, %s)""",
                    (product, size, batch_id)
                )
            unmapped += 1

    if not dry_run:
        cur.execute(
            "UPDATE import_batches SET deducted_count = %s, unmapped_count = %s WHERE batch_id = %s",
            (deducted, unmapped, batch_id)
        )
        conn.commit()

    cur.close()
    conn.close()

    print(f"  Results: {deducted} deducted, {unmapped} unmapped, {skipped} duplicates skipped")
    return deducted, unmapped, skipped


def main():
    parser = argparse.ArgumentParser(description='Daily import from Google Sheets')
    parser.add_argument('--sheet', help='Import from specific sheet only')
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing to DB')
    args = parser.parse_args()

    print(f"Culture Circle Daily Import - {datetime.now()}")

    sheets = [args.sheet] if args.sheet else SHEETS_TO_IMPORT
    total_deducted = 0
    total_unmapped = 0
    total_skipped = 0

    for sheet in sheets:
        d, u, s = import_orders(sheet, dry_run=args.dry_run)
        total_deducted += d
        total_unmapped += u
        total_skipped += s

    print(f"\n{'='*60}")
    print(f"TOTAL: {total_deducted} deducted, {total_unmapped} unmapped, {total_skipped} skipped")
    if args.dry_run:
        print("(DRY RUN - no changes written)")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
