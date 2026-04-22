import csv
import io
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db.database import query, execute, execute_returning, get_db

blanks_bp = Blueprint('blanks', __name__, url_prefix='/blanks')


@blanks_bp.route('/')
def list_blanks():
    view = request.args.get('view', 'grid')
    type_filter = request.args.get('type', '')
    stock_filter = request.args.get('stock', '')  # 'in_stock', 'out_of_stock', ''

    # All blanks (flat, for table view)
    blanks = query("""
        SELECT b.*, COUNT(m.mapping_id) AS sku_count
        FROM blank_master b
        LEFT JOIN sku_blank_mapping m ON m.blank_id = b.blank_id
        WHERE b.is_active = TRUE
        GROUP BY b.blank_id
        ORDER BY b.garment_type, b.color,
                 CASE b.size WHEN 'XS' THEN 1 WHEN 'S' THEN 2 WHEN 'M' THEN 3
                             WHEN 'L' THEN 4 WHEN 'XL' THEN 5 WHEN 'XXL' THEN 6 ELSE 7 END
    """)

    # Summary stats
    stats = query("""
        SELECT
            COUNT(*) AS total_skus,
            COUNT(DISTINCT garment_type) AS total_types,
            COUNT(DISTINCT color) AS total_colors,
            SUM(current_stock) AS total_stock,
            COUNT(*) FILTER (WHERE current_stock > 0) AS in_stock_skus,
            COUNT(*) FILTER (WHERE current_stock = 0) AS out_of_stock_skus,
            COUNT(DISTINCT garment_type) FILTER (WHERE current_stock > 0) AS types_in_stock
        FROM blank_master WHERE is_active = TRUE
    """, fetch='one')

    # Grouped data for grid view: garment_type -> color -> {sizes}
    size_order = ['XS', 'S', 'M', 'L', 'XL', 'XXL']
    garment_groups = query("""
        SELECT b.garment_type, b.color, b.size, b.current_stock, b.blank_id,
               COUNT(m.mapping_id) AS sku_count
        FROM blank_master b
        LEFT JOIN sku_blank_mapping m ON m.blank_id = b.blank_id
        WHERE b.is_active = TRUE
        GROUP BY b.blank_id, b.garment_type, b.color, b.size, b.current_stock
        ORDER BY b.garment_type, b.color,
                 CASE b.size WHEN 'XS' THEN 1 WHEN 'S' THEN 2 WHEN 'M' THEN 3
                             WHEN 'L' THEN 4 WHEN 'XL' THEN 5 WHEN 'XXL' THEN 6 ELSE 7 END
    """)

    # Build nested structure
    from collections import OrderedDict
    grouped = OrderedDict()
    for row in garment_groups:
        gtype = row['garment_type'] or 'Uncategorized'
        color = row['color'] or 'Unknown'

        if gtype not in grouped:
            grouped[gtype] = {'colors': OrderedDict(), 'total_stock': 0, 'total_skus': 0}
        if color not in grouped[gtype]['colors']:
            grouped[gtype]['colors'][color] = {'sizes': {}, 'total_stock': 0}

        grouped[gtype]['colors'][color]['sizes'][row['size']] = {
            'stock': int(row['current_stock'] or 0),
            'blank_id': row['blank_id'],
            'sku_count': row['sku_count'],
        }
        stock_val = int(row['current_stock'] or 0)
        grouped[gtype]['colors'][color]['total_stock'] += stock_val
        grouped[gtype]['total_stock'] += stock_val
        grouped[gtype]['total_skus'] += 1

    # Apply filters
    if type_filter:
        grouped = OrderedDict({k: v for k, v in grouped.items() if k == type_filter})
    if stock_filter == 'in_stock':
        filtered = OrderedDict()
        for gtype, data in grouped.items():
            filtered_colors = OrderedDict({c: d for c, d in data['colors'].items() if d['total_stock'] > 0})
            if filtered_colors:
                filtered[gtype] = {**data, 'colors': filtered_colors}
        grouped = filtered
    elif stock_filter == 'out_of_stock':
        filtered = OrderedDict()
        for gtype, data in grouped.items():
            filtered_colors = OrderedDict({c: d for c, d in data['colors'].items() if d['total_stock'] == 0})
            if filtered_colors:
                filtered[gtype] = {**data, 'colors': filtered_colors}
        grouped = filtered

    # Top stock by garment type (for chart)
    type_stock = query("""
        SELECT garment_type, SUM(current_stock) AS stock,
               COUNT(DISTINCT color) AS colors, COUNT(*) AS skus
        FROM blank_master WHERE is_active = TRUE
        GROUP BY garment_type ORDER BY SUM(current_stock) DESC
    """)

    # Available garment types for filter
    garment_types = query(
        "SELECT DISTINCT garment_type FROM blank_master WHERE is_active = TRUE AND garment_type IS NOT NULL ORDER BY garment_type"
    )

    return render_template('blanks/list.html',
                           blanks=blanks,
                           stats=stats,
                           grouped=grouped,
                           size_order=size_order,
                           type_stock=type_stock,
                           garment_types=garment_types,
                           view=view,
                           type_filter=type_filter,
                           stock_filter=stock_filter)


@blanks_bp.route('/add', methods=['GET', 'POST'])
def add_blank():
    if request.method == 'GET':
        return render_template('blanks/form.html', blank=None)

    blank_name = request.form.get('blank_name', '').strip()
    size = request.form.get('size', '').strip()
    garment_type = request.form.get('garment_type', '').strip() or None
    color = request.form.get('color', '').strip() or None
    current_stock = int(request.form.get('current_stock', 0))
    lead_time_days = int(request.form.get('lead_time_days', 28))
    min_batch_size = request.form.get('min_batch_size', '').strip()
    min_batch_size = int(min_batch_size) if min_batch_size else None
    reorder_level = request.form.get('reorder_level', '').strip()
    reorder_level = int(reorder_level) if reorder_level else None
    safety_buffer_days = int(request.form.get('safety_buffer_days', 7))

    if not blank_name or not size:
        flash('Blank name and size are required.', 'danger')
        return redirect(url_for('blanks.add_blank'))

    try:
        blank = execute_returning(
            """INSERT INTO blank_master (blank_name, garment_type, color, size, current_stock,
                   reorder_level, lead_time_days, min_batch_size, safety_buffer_days)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING blank_id""",
            (blank_name, garment_type, color, size, current_stock,
             reorder_level, lead_time_days, min_batch_size, safety_buffer_days)
        )
        # Log initial stock if > 0
        if current_stock > 0:
            execute(
                """INSERT INTO stock_movements (blank_id, movement_type, quantity, balance_after, notes, movement_date)
                   VALUES (%s, 'INITIAL', %s, %s, 'Initial stock entry', CURRENT_DATE)""",
                (blank['blank_id'], current_stock, current_stock)
            )
        flash(f"Blank '{blank_name} ({size})' created.", 'success')
    except Exception as e:
        if 'unique' in str(e).lower():
            flash(f"Blank '{blank_name} ({size})' already exists.", 'danger')
        else:
            flash(f"Error: {e}", 'danger')
        return redirect(url_for('blanks.add_blank'))

    return redirect(url_for('blanks.list_blanks'))


@blanks_bp.route('/edit/<int:blank_id>', methods=['GET', 'POST'])
def edit_blank(blank_id):
    blank = query("SELECT * FROM blank_master WHERE blank_id = %s", (blank_id,), fetch='one')
    if not blank:
        flash('Blank not found.', 'danger')
        return redirect(url_for('blanks.list_blanks'))

    if request.method == 'GET':
        return render_template('blanks/form.html', blank=blank)

    execute(
        """UPDATE blank_master SET
            blank_name = %s, garment_type = %s, color = %s, size = %s,
            reorder_level = %s, lead_time_days = %s, min_batch_size = %s,
            safety_buffer_days = %s, updated_at = NOW()
           WHERE blank_id = %s""",
        (
            request.form.get('blank_name', '').strip(),
            request.form.get('garment_type', '').strip() or None,
            request.form.get('color', '').strip() or None,
            request.form.get('size', '').strip(),
            int(request.form['reorder_level']) if request.form.get('reorder_level', '').strip() else None,
            int(request.form.get('lead_time_days', 28)),
            int(request.form['min_batch_size']) if request.form.get('min_batch_size', '').strip() else None,
            int(request.form.get('safety_buffer_days', 7)),
            blank_id
        )
    )
    flash('Blank updated.', 'success')
    return redirect(url_for('blanks.list_blanks'))


@blanks_bp.route('/stock/<int:blank_id>', methods=['GET', 'POST'])
def update_stock(blank_id):
    blank = query("SELECT * FROM blank_master WHERE blank_id = %s", (blank_id,), fetch='one')
    if not blank:
        flash('Blank not found.', 'danger')
        return redirect(url_for('blanks.list_blanks'))

    if request.method == 'GET':
        return render_template('blanks/stock.html', blank=blank)

    new_stock = int(request.form.get('new_stock', 0))
    old_stock = int(blank['current_stock'] or 0)
    diff = new_stock - old_stock
    notes = request.form.get('notes', '').strip() or 'Manual stock adjustment'

    execute(
        "UPDATE blank_master SET current_stock = %s, updated_at = NOW() WHERE blank_id = %s",
        (new_stock, blank_id)
    )
    execute(
        """INSERT INTO stock_movements (blank_id, movement_type, quantity, balance_after, notes, movement_date)
           VALUES (%s, 'MANUAL_ADJUSTMENT', %s, %s, %s, CURRENT_DATE)""",
        (blank_id, diff, new_stock, notes)
    )
    flash(f"Stock updated: {old_stock} -> {new_stock} ({'+' if diff >= 0 else ''}{diff})", 'success')
    return redirect(url_for('blanks.list_blanks'))


# --- SKU Mapping ---

@blanks_bp.route('/mappings')
def mappings():
    search = request.args.get('q', '').strip()
    blank_filter = request.args.get('blank', '')

    conditions = []
    params = []
    if search:
        conditions.append("(m.product ILIKE %s OR b.blank_name ILIKE %s OR b.garment_type ILIKE %s)")
        q = f"%{search}%"
        params.extend([q, q, q])
    if blank_filter:
        conditions.append("b.garment_type = %s")
        params.append(blank_filter)

    where = f"AND {' AND '.join(conditions)}" if conditions else ""

    maps = query(f"""
        SELECT m.*, b.blank_name, b.size AS blank_size, b.garment_type, b.color
        FROM sku_blank_mapping m
        JOIN blank_master b ON b.blank_id = m.blank_id
        WHERE TRUE {where}
        ORDER BY b.garment_type, b.color, m.product, m.size
    """, tuple(params) if params else None)

    # Group by blank (garment_type + color)
    from collections import OrderedDict
    grouped = OrderedDict()
    for m in maps:
        key = f"{m['garment_type'] or 'Unknown'} - {m['color'] or 'Unknown'}"
        if key not in grouped:
            grouped[key] = {
                'garment_type': m['garment_type'],
                'color': m['color'],
                'blank_name': m['blank_name'].rsplit(' - ', 1)[0] if ' - ' in m['blank_name'] else m['blank_name'],
                'products': []
            }
        grouped[key]['products'].append(m)

    # Stats
    mapping_stats = query("""
        SELECT COUNT(*) AS total,
               COUNT(DISTINCT m.product) AS unique_products,
               COUNT(DISTINCT b.garment_type) AS mapped_types
        FROM sku_blank_mapping m
        JOIN blank_master b ON b.blank_id = m.blank_id
    """, fetch='one')

    garment_types = query(
        "SELECT DISTINCT b.garment_type FROM sku_blank_mapping m JOIN blank_master b ON b.blank_id = m.blank_id WHERE b.garment_type IS NOT NULL ORDER BY b.garment_type"
    )

    return render_template('blanks/mappings.html',
                           mappings=maps,
                           grouped=grouped,
                           mapping_stats=mapping_stats,
                           garment_types=garment_types,
                           search=search,
                           blank_filter=blank_filter)


@blanks_bp.route('/mappings/add', methods=['GET', 'POST'])
def add_mapping():
    blanks = query("SELECT blank_id, blank_name, size FROM blank_master WHERE is_active = TRUE ORDER BY blank_name, size")

    if request.method == 'GET':
        # Pre-fill from unmapped SKU if provided
        product = request.args.get('product', '')
        brand = request.args.get('brand', '')
        size = request.args.get('size', '')
        return render_template('blanks/mapping_form.html', blanks=blanks, product=product, brand=brand, size=size)

    product = request.form.get('product', '').strip()
    brand = request.form.get('brand', '').strip() or None
    size = request.form.get('size', '').strip()
    blank_id = int(request.form.get('blank_id'))

    try:
        execute(
            """INSERT INTO sku_blank_mapping (product, brand, size, blank_id)
               VALUES (%s, %s, %s, %s)""",
            (product, brand, size, blank_id)
        )
        # Mark unmapped entries as mapped
        execute(
            "UPDATE unmapped_sku_log SET status = 'mapped' WHERE product = %s AND size = %s AND status = 'pending'",
            (product, size)
        )
        flash(f"Mapping created: {product} ({size}) -> blank #{blank_id}", 'success')
    except Exception as e:
        if 'unique' in str(e).lower():
            flash(f"Mapping for '{product} ({size})' already exists.", 'danger')
        else:
            flash(f"Error: {e}", 'danger')

    return redirect(url_for('blanks.mappings'))


# --- Bulk Import ---

@blanks_bp.route('/bulk', methods=['GET', 'POST'])
def bulk_import():
    if request.method == 'GET':
        return render_template('blanks/bulk.html')

    file = request.files.get('csv_file')
    import_type = request.form.get('import_type', 'blanks')

    if not file or not file.filename.endswith('.csv'):
        flash('Please upload a valid CSV file.', 'danger')
        return redirect(url_for('blanks.bulk_import'))

    text = file.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    db = get_db()
    count = 0
    errors = 0

    if import_type == 'blanks':
        for row in reader:
            try:
                blank_name = row.get('blank_name', '').strip()
                size = row.get('size', '').strip()
                if not blank_name or not size:
                    continue
                with db.cursor() as cur:
                    cur.execute(
                        """INSERT INTO blank_master (blank_name, garment_type, color, size, current_stock, lead_time_days, min_batch_size)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (blank_name, size) DO UPDATE SET
                              current_stock = EXCLUDED.current_stock,
                              garment_type = COALESCE(EXCLUDED.garment_type, blank_master.garment_type)""",
                        (
                            blank_name,
                            row.get('garment_type', '').strip() or None,
                            row.get('color', '').strip() or None,
                            size,
                            int(row.get('current_stock', 0) or 0),
                            int(row.get('lead_time_days', 28) or 28),
                            int(row['min_batch_size']) if row.get('min_batch_size', '').strip() else None,
                        )
                    )
                count += 1
            except Exception:
                errors += 1
    elif import_type == 'mappings':
        for row in reader:
            try:
                product = row.get('product', '').strip()
                size = row.get('size', '').strip()
                blank_name = row.get('blank_name', '').strip()
                blank_size = row.get('blank_size', size).strip()
                if not product or not size or not blank_name:
                    continue
                # Find the blank
                blank = query(
                    "SELECT blank_id FROM blank_master WHERE blank_name = %s AND size = %s",
                    (blank_name, blank_size), fetch='one'
                )
                if not blank:
                    errors += 1
                    continue
                with db.cursor() as cur:
                    cur.execute(
                        """INSERT INTO sku_blank_mapping (product, brand, size, blank_id)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT (product, size) DO NOTHING""",
                        (product, row.get('brand', '').strip() or None, size, blank['blank_id'])
                    )
                count += 1
            except Exception:
                errors += 1

    db.commit()
    flash(f"Bulk import: {count} rows imported, {errors} errors.", 'success' if errors == 0 else 'warning')
    return redirect(url_for('blanks.list_blanks'))


# --- Bulk Edit ---

@blanks_bp.route('/bulk-edit', methods=['GET', 'POST'])
def bulk_edit():
    if request.method == 'GET':
        # Get all garment types for the filter dropdown
        garment_types = query(
            "SELECT DISTINCT garment_type FROM blank_master WHERE is_active = TRUE AND garment_type IS NOT NULL ORDER BY garment_type"
        )
        selected_type = request.args.get('garment_type', '')
        selected_color = request.args.get('color', '')

        # Build filter query
        conditions = ["is_active = TRUE"]
        params = []
        if selected_type:
            conditions.append("garment_type = %s")
            params.append(selected_type)
        if selected_color:
            conditions.append("color = %s")
            params.append(selected_color)

        blanks = query(
            f"SELECT * FROM blank_master WHERE {' AND '.join(conditions)} ORDER BY blank_name, size",
            tuple(params) if params else None
        )

        colors = query(
            "SELECT DISTINCT color FROM blank_master WHERE is_active = TRUE AND color IS NOT NULL ORDER BY color"
        )

        return render_template('blanks/bulk_edit.html',
                               blanks=blanks,
                               garment_types=garment_types,
                               colors=colors,
                               selected_type=selected_type,
                               selected_color=selected_color)

    # POST: process bulk updates
    action = request.form.get('action', 'individual')
    db = get_db()
    count = 0

    if action == 'bulk_apply':
        # Apply same values to all filtered blanks
        blank_ids = request.form.getlist('blank_ids')
        lead_time = request.form.get('bulk_lead_time', '').strip()
        min_batch = request.form.get('bulk_min_batch', '').strip()
        safety_buffer = request.form.get('bulk_safety_buffer', '').strip()
        reorder_level = request.form.get('bulk_reorder_level', '').strip()

        if not blank_ids:
            flash('No blanks selected.', 'warning')
            return redirect(url_for('blanks.bulk_edit'))

        updates = []
        params_list = []
        if lead_time:
            updates.append("lead_time_days = %s")
            params_list.append(int(lead_time))
        if min_batch:
            updates.append("min_batch_size = %s")
            params_list.append(int(min_batch))
        if safety_buffer:
            updates.append("safety_buffer_days = %s")
            params_list.append(int(safety_buffer))
        if reorder_level:
            updates.append("reorder_level = %s")
            params_list.append(int(reorder_level))

        if updates:
            updates.append("updated_at = NOW()")
            id_placeholders = ','.join(['%s'] * len(blank_ids))
            sql = f"UPDATE blank_master SET {', '.join(updates)} WHERE blank_id IN ({id_placeholders})"
            params_list.extend([int(bid) for bid in blank_ids])
            with db.cursor() as cur:
                cur.execute(sql, tuple(params_list))
                count = cur.rowcount
            db.commit()
            flash(f'Updated {count} blanks.', 'success')
        else:
            flash('No fields to update.', 'warning')

    elif action == 'individual':
        # Update each blank row individually
        blank_ids = request.form.getlist('blank_ids')
        for bid in blank_ids:
            lead_time = request.form.get(f'lead_time_{bid}', '').strip()
            min_batch = request.form.get(f'min_batch_{bid}', '').strip()
            safety_buffer = request.form.get(f'safety_buffer_{bid}', '').strip()
            reorder_level = request.form.get(f'reorder_level_{bid}', '').strip()

            with db.cursor() as cur:
                cur.execute(
                    """UPDATE blank_master SET
                        lead_time_days = %s,
                        min_batch_size = %s,
                        safety_buffer_days = %s,
                        reorder_level = %s,
                        updated_at = NOW()
                       WHERE blank_id = %s""",
                    (
                        int(lead_time) if lead_time else 28,
                        int(min_batch) if min_batch else None,
                        int(safety_buffer) if safety_buffer else 7,
                        int(reorder_level) if reorder_level else None,
                        int(bid),
                    )
                )
            count += 1
        db.commit()
        flash(f'Updated {count} blanks individually.', 'success')

    return redirect(url_for('blanks.bulk_edit',
                            garment_type=request.form.get('filter_garment_type', ''),
                            color=request.form.get('filter_color', '')))
