from flask import Blueprint, render_template
from db.database import query

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
def index():
    # Get all active blanks with burn rate and days-to-stockout
    blanks = query("""
        SELECT b.*,
            COALESCE(burn.avg_burn, 0) AS avg_daily_burn,
            CASE
                WHEN COALESCE(burn.avg_burn, 0) = 0 AND b.current_stock > 0 THEN NULL
                WHEN COALESCE(burn.avg_burn, 0) = 0 AND b.current_stock <= 0 THEN 0
                ELSE ROUND(b.current_stock / burn.avg_burn, 1)
            END AS days_to_stockout,
            COALESCE(inflight.qty, 0) AS in_flight_qty
        FROM blank_master b
        LEFT JOIN LATERAL (
            SELECT CASE
                WHEN COALESCE(SUM(ABS(sm.quantity)), 0) /
                     GREATEST(COUNT(DISTINCT sm.movement_date), 1) > 3
                THEN COALESCE(SUM(ABS(sm.quantity)) FILTER (WHERE sm.movement_date >= CURRENT_DATE - 7), 0) / 7.0
                ELSE COALESCE(SUM(ABS(sm.quantity)) FILTER (WHERE sm.movement_date >= CURRENT_DATE - 21), 0) / 21.0
            END AS avg_burn
            FROM stock_movements sm
            WHERE sm.blank_id = b.blank_id
              AND sm.movement_type = 'CSV_DEDUCTION'
              AND sm.movement_date >= CURRENT_DATE - 21
        ) burn ON TRUE
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(r.qty_ordered), 0) AS qty
            FROM restock_orders r
            WHERE r.blank_id = b.blank_id
              AND r.status IN ('in_production', 'in_transit')
        ) inflight ON TRUE
        WHERE b.is_active = TRUE
        ORDER BY
            CASE
                WHEN COALESCE(burn.avg_burn, 0) = 0 AND b.current_stock <= 0 THEN 0
                WHEN COALESCE(burn.avg_burn, 0) = 0 THEN 9999
                ELSE ROUND(b.current_stock / burn.avg_burn, 1)
            END ASC
    """)

    # Compute status for each blank
    for b in blanks:
        b['status_color'] = _compute_status(b)

    # Staleness check: last import time
    last_import = query(
        "SELECT imported_at FROM import_batches ORDER BY imported_at DESC LIMIT 1",
        fetch='one'
    )

    # Unmapped count
    unmapped = query(
        "SELECT COUNT(*) AS cnt FROM unmapped_sku_log WHERE status = 'pending'",
        fetch='one'
    )

    # Summary stats
    stats = {
        'total_blanks': len(blanks),
        'red_count': sum(1 for b in blanks if b['status_color'] == 'red'),
        'yellow_count': sum(1 for b in blanks if b['status_color'] == 'yellow'),
        'green_count': sum(1 for b in blanks if b['status_color'] == 'green'),
        'gray_count': sum(1 for b in blanks if b['status_color'] == 'gray'),
        'unmapped_count': unmapped['cnt'] if unmapped else 0,
        'last_import': last_import['imported_at'] if last_import else None,
    }

    # Total stock
    total_stock = query(
        "SELECT SUM(current_stock) AS total FROM blank_master WHERE is_active = TRUE",
        fetch='one'
    )
    stats['total_stock'] = int(total_stock['total'] or 0) if total_stock else 0

    # Stock by garment type (compact heatmap for dashboard)
    type_stock = query("""
        SELECT garment_type,
               SUM(current_stock) AS stock,
               COUNT(DISTINCT color) AS colors,
               COUNT(*) FILTER (WHERE current_stock > 0) AS in_stock,
               COUNT(*) FILTER (WHERE current_stock = 0) AS out_of_stock
        FROM blank_master WHERE is_active = TRUE
        GROUP BY garment_type ORDER BY SUM(current_stock) DESC
    """)

    # Unfulfilled Shopify orders count
    shopify_stats = query("""
        SELECT
            COUNT(*) FILTER (WHERE fulfillment_status = 'unfulfilled') AS unfulfilled_orders,
            COUNT(*) AS total_orders
        FROM shopify_orders
    """, fetch='one')
    stats['unfulfilled_orders'] = shopify_stats['unfulfilled_orders'] if shopify_stats else 0
    stats['total_shopify_orders'] = shopify_stats['total_orders'] if shopify_stats else 0

    # Pending restock orders
    restocks = query("""
        SELECT r.*, b.blank_name, b.size
        FROM restock_orders r
        JOIN blank_master b ON b.blank_id = r.blank_id
        WHERE r.status IN ('in_production', 'in_transit')
        ORDER BY r.expected_delivery ASC NULLS LAST
    """)

    # Only show blanks with issues (red/yellow) or with burn rate on dashboard
    # instead of all 462
    attention_blanks = [b for b in blanks if b['status_color'] in ('red', 'yellow')]

    return render_template('dashboard.html',
                           blanks=blanks,
                           attention_blanks=attention_blanks,
                           type_stock=type_stock,
                           stats=stats,
                           restocks=restocks)


def _compute_status(blank):
    stock = float(blank['current_stock'] or 0)
    burn = float(blank['avg_daily_burn'] or 0)
    days = blank['days_to_stockout']
    lead = blank['lead_time_days'] or 28
    buffer = blank['safety_buffer_days'] or 7
    reorder = blank['reorder_level']

    # Manual override check
    if reorder is not None and stock <= float(reorder):
        return 'red' if stock == 0 else 'yellow'

    # Zero burn cases
    if burn == 0 and stock <= 0:
        return 'red'
    if burn == 0:
        return 'gray'

    # Days-based
    if days is not None:
        d = float(days)
        if d <= lead:
            return 'red'
        if d <= lead + buffer:
            return 'yellow'
    return 'green'
