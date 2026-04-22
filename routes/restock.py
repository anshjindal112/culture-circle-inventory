from flask import Blueprint, render_template, request, redirect, url_for, flash
from db.database import query, execute

restock_bp = Blueprint('restock', __name__, url_prefix='/restock')


@restock_bp.route('/')
def list_restocks():
    orders = query("""
        SELECT r.*, b.blank_name, b.size, b.current_stock
        FROM restock_orders r
        JOIN blank_master b ON b.blank_id = r.blank_id
        ORDER BY
            CASE r.status
                WHEN 'in_production' THEN 1
                WHEN 'in_transit' THEN 2
                WHEN 'received' THEN 3
                WHEN 'cancelled' THEN 4
            END,
            r.expected_delivery ASC NULLS LAST
    """)
    return render_template('restock/list.html', orders=orders)


@restock_bp.route('/new', methods=['GET', 'POST'])
def new_restock():
    blanks = query(
        "SELECT blank_id, blank_name, size, current_stock FROM blank_master WHERE is_active = TRUE ORDER BY blank_name, size"
    )
    if request.method == 'GET':
        pre_blank = request.args.get('blank_id', '')
        return render_template('restock/form.html', blanks=blanks, pre_blank=pre_blank)

    blank_id = int(request.form.get('blank_id'))
    qty = int(request.form.get('qty_ordered'))
    expected = request.form.get('expected_delivery') or None
    notes = request.form.get('notes', '').strip() or None

    execute(
        """INSERT INTO restock_orders (blank_id, qty_ordered, expected_delivery, notes)
           VALUES (%s, %s, %s, %s)""",
        (blank_id, qty, expected, notes)
    )
    flash(f"Restock order created: {qty} units.", 'success')
    return redirect(url_for('restock.list_restocks'))


@restock_bp.route('/receive/<int:restock_id>', methods=['GET', 'POST'])
def receive(restock_id):
    order = query("""
        SELECT r.*, b.blank_name, b.size
        FROM restock_orders r
        JOIN blank_master b ON b.blank_id = r.blank_id
        WHERE r.restock_id = %s
    """, (restock_id,), fetch='one')

    if not order:
        flash('Restock order not found.', 'danger')
        return redirect(url_for('restock.list_restocks'))

    if request.method == 'GET':
        return render_template('restock/receive.html', order=order)

    qty_received = int(request.form.get('qty_received', order['qty_ordered']))
    actual_date = request.form.get('actual_delivery') or None

    # Update restock order
    execute(
        """UPDATE restock_orders SET status = 'received', actual_delivery = COALESCE(%s, CURRENT_DATE), updated_at = NOW()
           WHERE restock_id = %s""",
        (actual_date, restock_id)
    )

    # Add stock
    execute(
        "UPDATE blank_master SET current_stock = current_stock + %s, updated_at = NOW() WHERE blank_id = %s",
        (qty_received, order['blank_id'])
    )

    # Get new balance
    blank = query("SELECT current_stock FROM blank_master WHERE blank_id = %s", (order['blank_id'],), fetch='one')

    # Log movement
    execute(
        """INSERT INTO stock_movements (blank_id, movement_type, quantity, balance_after, reference_type, reference_id, notes, movement_date)
           VALUES (%s, 'RESTOCK', %s, %s, 'restock_order', %s, %s, CURRENT_DATE)""",
        (order['blank_id'], qty_received, blank['current_stock'], restock_id,
         f"Restock received: {qty_received} units")
    )

    flash(f"Received {qty_received} units of {order['blank_name']} ({order['size']}). Stock updated.", 'success')
    return redirect(url_for('restock.list_restocks'))
