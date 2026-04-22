import csv
import hashlib
import io
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db.database import query, execute, execute_returning, get_db
from services.auto_mapper import auto_map_and_save, auto_map_product, suggest_blank_from_image

csv_bp = Blueprint('csv_import', __name__, url_prefix='/csv')


@csv_bp.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'GET':
        return render_template('csv_import/upload.html')

    file = request.files.get('csv_file')
    if not file or not file.filename.endswith('.csv'):
        flash('Please upload a valid CSV file.', 'danger')
        return redirect(url_for('csv_import.upload'))

    content = file.read()
    file_hash = hashlib.sha256(content).hexdigest()

    # Duplicate check
    existing = query(
        "SELECT batch_id FROM import_batches WHERE file_hash = %s",
        (file_hash,), fetch='one'
    )
    if existing:
        flash('This CSV has already been imported. Duplicate upload blocked.', 'warning')
        return redirect(url_for('csv_import.upload'))

    # Parse CSV
    text = content.decode('utf-8-sig')  # Handle BOM
    reader = csv.DictReader(io.StringIO(text))

    orders = []
    for row in reader:
        status = (row.get('Status') or '').strip().lower()
        if status == 'cancelled':
            continue  # Skip cancelled orders

        order_id = (row.get('Order ID') or '').strip()
        if not order_id:
            continue

        orders.append({
            'order_id': order_id,
            'inventory_id': (row.get('Inventory ID') or '').strip(),
            'product': (row.get('Product') or '').strip(),
            'brand': (row.get('Brand') or '').strip(),
            'size': (row.get('Size') or '').strip(),
            'status': status,
            'order_date': (row.get('Created At') or '').strip() or None,
        })

    if not orders:
        flash('No valid orders found in CSV.', 'warning')
        return redirect(url_for('csv_import.upload'))

    # Create import batch
    batch = execute_returning(
        """INSERT INTO import_batches (file_hash, file_name, order_count)
           VALUES (%s, %s, %s) RETURNING batch_id""",
        (file_hash, file.filename, len(orders))
    )
    batch_id = batch['batch_id']

    # Process each order
    db = get_db()
    deducted = 0
    unmapped = 0
    skipped_dupes = 0

    for o in orders:
        # Skip if order already imported (idempotency)
        existing_order = query(
            "SELECT order_id FROM daily_orders WHERE order_id = %s",
            (o['order_id'],), fetch='one'
        )
        if existing_order:
            skipped_dupes += 1
            continue

        # Find SKU-to-blank mapping (existing mapping OR auto-map)
        mapping = query(
            "SELECT blank_id FROM sku_blank_mapping WHERE product = %s AND size = %s",
            (o['product'], o['size']), fetch='one'
        )

        if mapping:
            blank_id = mapping['blank_id']
        else:
            # Try auto-mapping via keyword extraction
            blank_id = auto_map_and_save(o['product'], o['brand'], o['size'], query, execute)

        # Insert order record
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO daily_orders (order_id, inventory_id, product, brand, size, status, order_date, blank_id, import_batch_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (order_id) DO NOTHING""",
                (o['order_id'], o['inventory_id'], o['product'], o['brand'],
                 o['size'], o['status'], o['order_date'], blank_id, batch_id)
            )

        if blank_id:
            # Deduct stock
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE blank_master SET current_stock = current_stock - 1, updated_at = NOW() WHERE blank_id = %s",
                    (blank_id,)
                )
                # Get new balance
                cur.execute("SELECT current_stock FROM blank_master WHERE blank_id = %s", (blank_id,))
                new_balance = cur.fetchone()[0]
                # Log movement
                cur.execute(
                    """INSERT INTO stock_movements (blank_id, movement_type, quantity, balance_after, reference_type, reference_id, notes, movement_date)
                       VALUES (%s, 'CSV_DEDUCTION', -1, %s, 'import_batch', %s, %s, CURRENT_DATE)""",
                    (blank_id, new_balance, batch_id, f"Order {o['order_id']}")
                )
            deducted += 1
        else:
            # Log unmapped SKU with auto-mapper suggestions
            info = auto_map_product(o['product'], o['size'])
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO unmapped_sku_log (product, brand, size, import_batch_id)
                       VALUES (%s, %s, %s, %s)""",
                    (o['product'], o['brand'], o['size'], batch_id)
                )
            unmapped += 1

    # Update batch stats
    with db.cursor() as cur:
        cur.execute(
            "UPDATE import_batches SET deducted_count = %s, unmapped_count = %s WHERE batch_id = %s",
            (deducted, unmapped, batch_id)
        )
    db.commit()

    flash(
        f"Import complete: {len(orders)} orders, {deducted} deducted, "
        f"{unmapped} unmapped, {skipped_dupes} duplicates skipped.",
        'success'
    )

    if unmapped > 0:
        return redirect(url_for('csv_import.unmapped'))
    return redirect(url_for('dashboard.index'))


@csv_bp.route('/unmapped')
def unmapped():
    items = query("""
        SELECT u.*, COUNT(*) OVER (PARTITION BY u.product, u.size) as total_qty
        FROM unmapped_sku_log u
        WHERE u.status = 'pending'
        ORDER BY u.product, u.size
    """)
    # Deduplicate and enrich with auto-mapper suggestions
    seen = set()
    unique_items = []
    for item in items:
        key = (item['product'], item['size'])
        if key not in seen:
            seen.add(key)
            # Add auto-mapper suggestions for display
            info = auto_map_product(item['product'], item['size'])
            item['suggested_garment'] = info.get('garment_type')
            item['suggested_color'] = info.get('color')
            # Image fallback: only fetch if no garment type detected
            item['image_url'] = None
            if not info.get('garment_type'):
                img_info = suggest_blank_from_image(item['product'], item['size'], query)
                if img_info:
                    item['image_url'] = img_info.get('image_url')
            unique_items.append(item)
    return render_template('csv_import/unmapped.html', items=unique_items)


@csv_bp.route('/unmapped/skip/<int:item_id>', methods=['POST'])
def skip_unmapped(item_id):
    row = query("SELECT product, size FROM unmapped_sku_log WHERE id = %s", (item_id,), fetch='one')
    if row:
        execute(
            "UPDATE unmapped_sku_log SET status = 'skipped' WHERE product = %s AND size = %s AND status = 'pending'",
            (row['product'], row['size'])
        )
        flash(f"Skipped: {row['product']} ({row['size']})", 'info')
    return redirect(url_for('csv_import.unmapped'))


@csv_bp.route('/history')
def history():
    batches = query("SELECT * FROM import_batches ORDER BY imported_at DESC LIMIT 30")
    return render_template('csv_import/history.html', batches=batches)
