"""
Shopify Orders routes — view, filter, and sync orders from all connected stores.
Supports bidirectional status updates.
"""

import csv
import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, Response, current_app
from db.database import query, execute, execute_returning, get_db
from services.shopify_service import ShopifyClient, normalize_order
from config import get_shopify_stores


def _today_in_tz():
    """Return today's date in the app's configured timezone (default Asia/Kolkata).
    Vercel runs in UTC; this keeps "Today" matching the user's clock."""
    tz = ZoneInfo(current_app.config.get('APP_TIMEZONE') or 'Asia/Kolkata')
    return datetime.now(tz).date()

orders_bp = Blueprint('orders', __name__, url_prefix='/orders')


# ── List / Filter ─────────────────────────────────────────────────────────

@orders_bp.route('/')
def list_orders():
    """Main orders page with filtering."""
    store_filter = request.args.get('store', '')
    status_filter = request.args.get('status', '')
    fulfillment_filter = request.args.get('fulfillment', '')
    search = request.args.get('q', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    page = int(request.args.get('page', 1))
    per_page = 50

    where, params = _build_orders_filter(
        store_filter, status_filter, fulfillment_filter, search,
        date_from, date_to,
    )

    # Total count
    total = query(
        f"SELECT COUNT(*) AS cnt FROM shopify_orders o {where}",
        tuple(params) if params else None,
        fetch='one'
    )
    total_count = total['cnt'] if total else 0
    total_pages = max(1, (total_count + per_page - 1) // per_page)

    # Fetch orders
    offset = (page - 1) * per_page
    orders = query(f"""
        SELECT o.*,
               (SELECT COUNT(*) FROM shopify_order_items i WHERE i.order_id = o.id) AS item_count
        FROM shopify_orders o
        {where}
        ORDER BY o.shopify_created_at DESC NULLS LAST
        LIMIT %s OFFSET %s
    """, tuple(params + [per_page, offset]) if params else (per_page, offset))

    # Get available stores for filter dropdown
    stores = query("SELECT DISTINCT store_prefix FROM shopify_orders ORDER BY store_prefix")

    # All configured stores with their sync status
    all_stores = get_shopify_stores()
    store_status = query("SELECT prefix, store_name, last_synced_at FROM shopify_stores ORDER BY prefix")
    store_status_map = {s['prefix']: s for s in store_status}

    # Last sync errors to determine which stores have permission issues
    last_sync_log = query(
        "SELECT errors FROM shopify_sync_log ORDER BY started_at DESC LIMIT 1",
        fetch='one'
    )
    error_stores = set()
    if last_sync_log and last_sync_log.get('errors'):
        for err_part in last_sync_log['errors'].split(';'):
            err_part = err_part.strip()
            if '403' in err_part or 'permission' in err_part.lower():
                store_name = err_part.split(':')[0].strip()
                error_stores.add(store_name)

    configured_stores = []
    for s in all_stores:
        info = store_status_map.get(s['prefix'], {})
        has_error = s['name'] in error_stores
        order_count_row = query(
            "SELECT COUNT(*) as cnt FROM shopify_orders WHERE store_prefix = %s",
            (s['prefix'],), fetch='one'
        )
        configured_stores.append({
            'prefix': s['prefix'],
            'name': s['name'],
            'last_synced': info.get('last_synced_at'),
            'has_permission': not has_error,
            'order_count': order_count_row['cnt'] if order_count_row else 0,
        })

    # Stats
    stats = _get_order_stats()

    # Last sync info
    last_sync = query(
        "SELECT * FROM shopify_sync_log ORDER BY started_at DESC LIMIT 1",
        fetch='one'
    )

    today_d = _today_in_tz()
    return render_template('orders/list.html',
                           orders=orders,
                           stores=stores,
                           configured_stores=configured_stores,
                           stats=stats,
                           last_sync=last_sync,
                           store_filter=store_filter,
                           status_filter=status_filter,
                           fulfillment_filter=fulfillment_filter,
                           search=search,
                           date_from=date_from,
                           date_to=date_to,
                           today=today_d.isoformat(),
                           date_yesterday=(today_d - timedelta(days=1)).isoformat(),
                           date_7d_ago=(today_d - timedelta(days=6)).isoformat(),
                           date_30d_ago=(today_d - timedelta(days=29)).isoformat(),
                           page=page,
                           total_pages=total_pages,
                           total_count=total_count)


# ── Order Detail ──────────────────────────────────────────────────────────

@orders_bp.route('/<int:order_id>')
def order_detail(order_id):
    """Full order detail page."""
    order = query("SELECT * FROM shopify_orders WHERE id = %s", (order_id,), fetch='one')
    if not order:
        flash('Order not found.', 'danger')
        return redirect(url_for('orders.list_orders'))

    items = query(
        "SELECT * FROM shopify_order_items WHERE order_id = %s ORDER BY id",
        (order_id,)
    )
    fulfillments = query(
        "SELECT * FROM shopify_fulfillments WHERE order_id = %s ORDER BY created_at",
        (order_id,)
    )

    return render_template('orders/detail.html',
                           order=order, items=items, fulfillments=fulfillments)


# ── Status Update (Dashboard → Shopify) ──────────────────────────────────

@orders_bp.route('/<int:order_id>/update-status', methods=['POST'])
def update_status(order_id):
    """Update fulfillment status — syncs back to Shopify."""
    order = query("SELECT * FROM shopify_orders WHERE id = %s", (order_id,), fetch='one')
    if not order:
        flash('Order not found.', 'danger')
        return redirect(url_for('orders.list_orders'))

    action = request.form.get('action', '')
    tracking_number = request.form.get('tracking_number', '').strip()
    tracking_company = request.form.get('tracking_company', '').strip()
    tracking_url = request.form.get('tracking_url', '').strip()

    # Get Shopify client for this store
    client = _get_client(order['store_prefix'])
    if not client:
        flash(f"Store {order['store_prefix']} not configured.", 'danger')
        return redirect(url_for('orders.order_detail', order_id=order_id))

    try:
        if action == 'fulfill':
            result = client.create_fulfillment(
                order['shopify_order_id'],
                tracking_number=tracking_number or None,
                tracking_company=tracking_company or None,
                tracking_url=tracking_url or None,
            )
            if result:
                execute(
                    "UPDATE shopify_orders SET fulfillment_status = 'fulfilled', updated_at = NOW() WHERE id = %s",
                    (order_id,)
                )
                # Save fulfillment record
                execute(
                    """INSERT INTO shopify_fulfillments (order_id, shopify_fulfillment_id, status,
                           tracking_number, tracking_company, tracking_url, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, NOW())
                       ON CONFLICT (order_id, shopify_fulfillment_id) DO NOTHING""",
                    (order_id, result['id'], result.get('status', 'success'),
                     tracking_number, tracking_company, tracking_url)
                )
                flash('Order fulfilled on Shopify.', 'success')
            else:
                flash('No fulfillable items found on Shopify.', 'warning')

        elif action == 'cancel':
            reason = request.form.get('cancel_reason', 'other')
            client.cancel_order(order['shopify_order_id'], reason=reason)
            execute(
                "UPDATE shopify_orders SET fulfillment_status = 'cancelled', cancel_reason = %s, cancelled_at = NOW(), updated_at = NOW() WHERE id = %s",
                (reason, order_id)
            )
            flash('Order cancelled on Shopify.', 'success')

        elif action == 'close':
            client.close_order(order['shopify_order_id'])
            execute(
                "UPDATE shopify_orders SET closed_at = NOW(), updated_at = NOW() WHERE id = %s",
                (order_id,)
            )
            flash('Order closed on Shopify.', 'success')

        elif action == 'reopen':
            client.reopen_order(order['shopify_order_id'])
            execute(
                "UPDATE shopify_orders SET closed_at = NULL, updated_at = NOW() WHERE id = %s",
                (order_id,)
            )
            flash('Order reopened on Shopify.', 'success')

        elif action == 'update_note':
            note = request.form.get('note', '')
            client.update_order(order['shopify_order_id'], {"note": note})
            execute(
                "UPDATE shopify_orders SET note = %s, updated_at = NOW() WHERE id = %s",
                (note, order_id)
            )
            flash('Note updated on Shopify.', 'success')

        elif action == 'update_tags':
            tags = request.form.get('tags', '')
            client.update_order(order['shopify_order_id'], {"tags": tags})
            execute(
                "UPDATE shopify_orders SET tags = %s, updated_at = NOW() WHERE id = %s",
                (tags, order_id)
            )
            flash('Tags updated on Shopify.', 'success')

    except Exception as e:
        flash(f'Shopify API error: {str(e)}', 'danger')

    return redirect(url_for('orders.order_detail', order_id=order_id))


# ── Sync (Shopify → Dashboard) ───────────────────────────────────────────

@orders_bp.route('/sync', methods=['POST'])
def sync_orders():
    """Pull latest orders from all Shopify stores."""
    store_filter = request.form.get('store', '')
    stores = get_shopify_stores()

    if store_filter:
        stores = [s for s in stores if s['prefix'] == store_filter]

    total_fetched = 0
    total_new = 0
    total_updated = 0
    errors = []

    for store in stores:
        try:
            client = ShopifyClient(store['domain'], store['access_token'], store['name'])
            raw_orders = client.get_orders(status="any")

            fetched = len(raw_orders)
            new_count = 0
            updated_count = 0

            for raw in raw_orders:
                normalized = normalize_order(raw, store['name'], store['prefix'])
                result = _upsert_order(normalized)
                if result == 'new':
                    new_count += 1
                elif result == 'updated':
                    updated_count += 1

            # Update store sync time
            execute("""
                INSERT INTO shopify_stores (prefix, store_name, domain, last_synced_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (prefix) DO UPDATE SET last_synced_at = NOW()
            """, (store['prefix'], store['name'], store['domain']))

            total_fetched += fetched
            total_new += new_count
            total_updated += updated_count

        except Exception as e:
            err_str = str(e)
            if '403' in err_str:
                errors.append(f"{store['name']}: No orders permission (403 Forbidden)")
            else:
                errors.append(f"{store['name']}: {err_str}")

    # Log sync run
    execute("""
        INSERT INTO shopify_sync_log (store_prefix, orders_fetched, orders_new, orders_updated, errors, completed_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
    """, (
        store_filter or 'ALL',
        total_fetched, total_new, total_updated,
        '; '.join(errors) if errors else None
    ))

    permission_errors = [e for e in errors if '403' in e or 'permission' in e.lower()]
    other_errors = [e for e in errors if e not in permission_errors]

    if permission_errors:
        stores_needing_auth = [e.split(':')[0] for e in permission_errors]
        flash(
            f'{len(permission_errors)} store(s) need read_orders permission re-authorized in Shopify: '
            f'{", ".join(stores_needing_auth)}. Go to each store\'s Shopify Admin > Settings > Apps and re-install the app with orders access.',
            'warning'
        )
    if other_errors:
        flash(f'Sync errors: {"; ".join(other_errors)}', 'danger')
    if total_fetched > 0:
        flash(f'Sync complete: {total_fetched} orders fetched, {total_new} new, {total_updated} updated.', 'success')
    elif not errors:
        flash('Sync complete: no orders found in any store.', 'info')

    return redirect(url_for('orders.list_orders'))


# ── Refresh Single Order ─────────────────────────────────────────────────

@orders_bp.route('/<int:order_id>/refresh', methods=['POST'])
def refresh_order(order_id):
    """Re-fetch a single order from Shopify."""
    order = query("SELECT * FROM shopify_orders WHERE id = %s", (order_id,), fetch='one')
    if not order:
        flash('Order not found.', 'danger')
        return redirect(url_for('orders.list_orders'))

    client = _get_client(order['store_prefix'])
    if not client:
        flash(f"Store {order['store_prefix']} not configured.", 'danger')
        return redirect(url_for('orders.order_detail', order_id=order_id))

    try:
        raw = client.get_order(order['shopify_order_id'])
        if raw:
            store = next((s for s in get_shopify_stores() if s['prefix'] == order['store_prefix']), None)
            normalized = normalize_order(raw, store['name'] if store else order['store_prefix'], order['store_prefix'])
            _upsert_order(normalized)
            flash('Order refreshed from Shopify.', 'success')
        else:
            flash('Order not found on Shopify.', 'warning')
    except Exception as e:
        flash(f'Error refreshing: {str(e)}', 'danger')

    return redirect(url_for('orders.order_detail', order_id=order_id))


# ── API endpoint for AJAX status checks ──────────────────────────────────

@orders_bp.route('/api/stats')
def api_stats():
    return jsonify(_get_order_stats())


# ── CSV Export ────────────────────────────────────────────────────────────

@orders_bp.route('/export.csv')
def export_csv():
    """Stream the currently-filtered orders as a CSV for offline fulfilment.

    Honors the same `store`, `status`, `fulfillment`, `q` query params as
    the list page. Includes one row per line item (so SKU + qty are usable
    by whoever is packing).
    """
    store_filter = request.args.get('store', '')
    status_filter = request.args.get('status', '')
    fulfillment_filter = request.args.get('fulfillment', '')
    search = request.args.get('q', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    where, params = _build_orders_filter(
        store_filter, status_filter, fulfillment_filter, search,
        date_from, date_to,
    )

    rows = query(f"""
        SELECT
            o.name AS order_name, o.order_number, o.store_prefix,
            o.shopify_created_at, o.financial_status, o.fulfillment_status,
            o.currency, o.total_price,
            o.customer_first_name, o.customer_last_name,
            o.email, COALESCE(o.shipping_phone, o.phone, o.customer_phone) AS phone,
            o.shipping_name, o.shipping_address1, o.shipping_address2,
            o.shipping_city, o.shipping_province, o.shipping_zip, o.shipping_country,
            o.note, o.tags,
            i.sku, i.title AS item_title, i.variant_title, i.quantity, i.price
        FROM shopify_orders o
        LEFT JOIN shopify_order_items i ON i.order_id = o.id
        {where}
        ORDER BY o.shopify_created_at DESC NULLS LAST, o.id DESC, i.id ASC
    """, tuple(params) if params else None)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        'Order', 'Order #', 'Store', 'Created', 'Payment', 'Fulfillment',
        'Currency', 'Total',
        'Customer Name', 'Email', 'Phone',
        'Ship To', 'Address 1', 'Address 2', 'City', 'Province', 'ZIP', 'Country',
        'SKU', 'Item', 'Variant', 'Qty', 'Unit Price',
        'Note', 'Tags',
    ])

    for r in rows:
        writer.writerow([
            r.get('order_name') or '',
            r.get('order_number') or '',
            r.get('store_prefix') or '',
            r['shopify_created_at'].strftime('%Y-%m-%d %H:%M') if r.get('shopify_created_at') else '',
            r.get('financial_status') or '',
            r.get('fulfillment_status') or '',
            r.get('currency') or '',
            f"{float(r['total_price']):.2f}" if r.get('total_price') is not None else '',
            f"{r.get('customer_first_name') or ''} {r.get('customer_last_name') or ''}".strip(),
            r.get('email') or '',
            r.get('phone') or '',
            r.get('shipping_name') or '',
            r.get('shipping_address1') or '',
            r.get('shipping_address2') or '',
            r.get('shipping_city') or '',
            r.get('shipping_province') or '',
            r.get('shipping_zip') or '',
            r.get('shipping_country') or '',
            r.get('sku') or '',
            r.get('item_title') or '',
            r.get('variant_title') or '',
            r.get('quantity') or '',
            f"{float(r['price']):.2f}" if r.get('price') is not None else '',
            r.get('note') or '',
            r.get('tags') or '',
        ])

    parts = ['shopify-orders']
    if store_filter:
        parts.append(store_filter)
    if fulfillment_filter:
        parts.append(fulfillment_filter)
    if status_filter:
        parts.append(status_filter)
    if date_from and date_to and date_from == date_to:
        parts.append(date_from)
    elif date_from or date_to:
        parts.append(f"{date_from or 'start'}_to_{date_to or 'now'}")
    parts.append(datetime.now().strftime('%H%M'))
    filename = '-'.join(parts) + '.csv'

    return Response(
        buf.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ── Helpers ───────────────────────────────────────────────────────────────

def _build_orders_filter(store_filter, status_filter, fulfillment_filter, search,
                         date_from='', date_to=''):
    """Shared WHERE clause + params builder used by list_orders and export_csv.

    date_from / date_to are ISO date strings (YYYY-MM-DD). They filter on
    `shopify_created_at::date` inclusively. Invalid dates are silently ignored
    so a typo doesn't 500 the page.
    """
    conditions = ["o.financial_status NOT IN ('voided', 'refunded')", "o.cancelled_at IS NULL"]
    params = []

    if store_filter:
        conditions.append("o.store_prefix = %s")
        params.append(store_filter)
    if status_filter:
        conditions.append("o.financial_status = %s")
        params.append(status_filter)
    if fulfillment_filter:
        conditions.append("o.fulfillment_status = %s")
        params.append(fulfillment_filter)
    if search:
        conditions.append(
            "(o.name ILIKE %s OR o.email ILIKE %s OR o.customer_first_name ILIKE %s "
            "OR o.customer_last_name ILIKE %s OR o.shipping_phone ILIKE %s OR o.phone ILIKE %s)"
        )
        q = f"%{search}%"
        params.extend([q, q, q, q, q, q])
    if date_from:
        try:
            datetime.strptime(date_from, '%Y-%m-%d')
            conditions.append("o.shopify_created_at::date >= %s")
            params.append(date_from)
        except ValueError:
            pass
    if date_to:
        try:
            datetime.strptime(date_to, '%Y-%m-%d')
            conditions.append("o.shopify_created_at::date <= %s")
            params.append(date_to)
        except ValueError:
            pass

    where = f"WHERE {' AND '.join(conditions)}"
    return where, params


def _get_client(store_prefix):
    """Get a ShopifyClient for a given store prefix."""
    stores = get_shopify_stores()
    store = next((s for s in stores if s['prefix'] == store_prefix), None)
    if store:
        return ShopifyClient(store['domain'], store['access_token'], store['name'])
    return None


def _get_order_stats():
    """Compute summary stats for orders — excludes voided/cancelled."""
    stats_rows = query("""
        SELECT
            COUNT(*) AS total_orders,
            COUNT(*) FILTER (WHERE fulfillment_status = 'unfulfilled') AS unfulfilled,
            COUNT(*) FILTER (WHERE fulfillment_status = 'fulfilled') AS fulfilled,
            COUNT(*) FILTER (WHERE fulfillment_status = 'partial') AS partial,
            COUNT(*) FILTER (WHERE financial_status = 'paid') AS paid,
            COUNT(*) FILTER (WHERE financial_status = 'pending') AS pending_payment,
            COUNT(DISTINCT store_prefix) AS store_count,
            COALESCE(SUM(total_price), 0) AS total_revenue
        FROM shopify_orders
        WHERE financial_status NOT IN ('voided', 'refunded')
          AND cancelled_at IS NULL
    """, fetch='one')
    return stats_rows or {}


def _upsert_order(normalized):
    """Insert or update an order. Returns 'new', 'updated', or 'unchanged'."""
    db = get_db()
    existing = query(
        "SELECT id, shopify_updated_at FROM shopify_orders WHERE shopify_order_id = %s AND store_prefix = %s",
        (normalized['shopify_order_id'], normalized['store_prefix']),
        fetch='one'
    )

    if existing:
        local_id = existing['id']
        # Update if Shopify data is newer
        with db.cursor() as cur:
            cur.execute("""
                UPDATE shopify_orders SET
                    order_number = %s, name = %s, email = %s, phone = %s,
                    financial_status = %s, fulfillment_status = %s,
                    total_price = %s, subtotal_price = %s, total_tax = %s, total_discounts = %s,
                    currency = %s, tags = %s, note = %s,
                    cancel_reason = %s, cancelled_at = %s, closed_at = %s,
                    customer_id = %s, customer_first_name = %s, customer_last_name = %s,
                    customer_email = %s, customer_phone = %s,
                    customer_orders_count = %s, customer_total_spent = %s,
                    shipping_name = %s, shipping_address1 = %s, shipping_address2 = %s,
                    shipping_city = %s, shipping_province = %s, shipping_zip = %s,
                    shipping_country = %s, shipping_phone = %s,
                    billing_name = %s, billing_address1 = %s, billing_address2 = %s,
                    billing_city = %s, billing_province = %s, billing_zip = %s, billing_country = %s,
                    shopify_created_at = %s, shopify_updated_at = %s, processed_at = %s,
                    synced_at = NOW(), updated_at = NOW()
                WHERE id = %s
            """, (
                normalized['order_number'], normalized['name'], normalized['email'], normalized['phone'],
                normalized['financial_status'], normalized['fulfillment_status'],
                normalized['total_price'], normalized['subtotal_price'],
                normalized['total_tax'], normalized['total_discounts'],
                normalized['currency'], normalized['tags'], normalized['note'],
                normalized['cancel_reason'], normalized['cancelled_at'], normalized['closed_at'],
                normalized['customer_id'], normalized['customer_first_name'], normalized['customer_last_name'],
                normalized['customer_email'], normalized['customer_phone'],
                normalized['customer_orders_count'], normalized['customer_total_spent'],
                normalized['shipping_name'], normalized['shipping_address1'], normalized['shipping_address2'],
                normalized['shipping_city'], normalized['shipping_province'], normalized['shipping_zip'],
                normalized['shipping_country'], normalized['shipping_phone'],
                normalized['billing_name'], normalized['billing_address1'], normalized['billing_address2'],
                normalized['billing_city'], normalized['billing_province'], normalized['billing_zip'],
                normalized['billing_country'],
                normalized['created_at'], normalized['updated_at'], normalized['processed_at'],
                local_id,
            ))
        result = 'updated'
    else:
        # Insert new
        row = execute_returning("""
            INSERT INTO shopify_orders (
                shopify_order_id, store_prefix, order_number, name, email, phone,
                financial_status, fulfillment_status,
                total_price, subtotal_price, total_tax, total_discounts,
                currency, tags, note, cancel_reason, cancelled_at, closed_at,
                customer_id, customer_first_name, customer_last_name,
                customer_email, customer_phone, customer_orders_count, customer_total_spent,
                shipping_name, shipping_address1, shipping_address2,
                shipping_city, shipping_province, shipping_zip, shipping_country, shipping_phone,
                billing_name, billing_address1, billing_address2,
                billing_city, billing_province, billing_zip, billing_country,
                shopify_created_at, shopify_updated_at, processed_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s
            ) RETURNING id
        """, (
            normalized['shopify_order_id'], normalized['store_prefix'],
            normalized['order_number'], normalized['name'], normalized['email'], normalized['phone'],
            normalized['financial_status'], normalized['fulfillment_status'],
            normalized['total_price'], normalized['subtotal_price'],
            normalized['total_tax'], normalized['total_discounts'],
            normalized['currency'], normalized['tags'], normalized['note'],
            normalized['cancel_reason'], normalized['cancelled_at'], normalized['closed_at'],
            normalized['customer_id'], normalized['customer_first_name'], normalized['customer_last_name'],
            normalized['customer_email'], normalized['customer_phone'],
            normalized['customer_orders_count'], normalized['customer_total_spent'],
            normalized['shipping_name'], normalized['shipping_address1'], normalized['shipping_address2'],
            normalized['shipping_city'], normalized['shipping_province'], normalized['shipping_zip'],
            normalized['shipping_country'], normalized['shipping_phone'],
            normalized['billing_name'], normalized['billing_address1'], normalized['billing_address2'],
            normalized['billing_city'], normalized['billing_province'], normalized['billing_zip'],
            normalized['billing_country'],
            normalized['created_at'], normalized['updated_at'], normalized['processed_at'],
        ))
        local_id = row['id']
        result = 'new'

    # Upsert line items
    with db.cursor() as cur:
        for li in normalized.get('line_items', []):
            cur.execute("""
                INSERT INTO shopify_order_items (
                    order_id, shopify_line_item_id, title, variant_title, sku,
                    quantity, price, total_discount, fulfillment_status, product_id, variant_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id, shopify_line_item_id) DO UPDATE SET
                    title = EXCLUDED.title, variant_title = EXCLUDED.variant_title,
                    sku = EXCLUDED.sku, quantity = EXCLUDED.quantity, price = EXCLUDED.price,
                    total_discount = EXCLUDED.total_discount,
                    fulfillment_status = EXCLUDED.fulfillment_status
            """, (
                local_id, li['shopify_line_item_id'], li['title'], li['variant_title'],
                li['sku'], li['quantity'], li['price'], li['total_discount'],
                li['fulfillment_status'], li['product_id'], li['variant_id'],
            ))

        # Upsert fulfillments
        for f in normalized.get('fulfillments', []):
            cur.execute("""
                INSERT INTO shopify_fulfillments (
                    order_id, shopify_fulfillment_id, status,
                    tracking_number, tracking_company, tracking_url, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id, shopify_fulfillment_id) DO UPDATE SET
                    status = EXCLUDED.status, tracking_number = EXCLUDED.tracking_number,
                    tracking_company = EXCLUDED.tracking_company, tracking_url = EXCLUDED.tracking_url
            """, (
                local_id, f['shopify_fulfillment_id'], f['status'],
                f['tracking_number'], f['tracking_company'], f['tracking_url'], f['created_at'],
            ))

    db.commit()
    return result
