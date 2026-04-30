from datetime import date

from flask import Blueprint, render_template
from db.database import query

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
def index():
    today = date.today().isoformat()
    # Orders-only landing page. Inventory/stock surface lives in the Google Sheet.
    shopify_stats = query("""
        SELECT
            COUNT(*) FILTER (WHERE financial_status NOT IN ('voided', 'refunded') AND cancelled_at IS NULL) AS total_orders,
            COUNT(*) FILTER (WHERE fulfillment_status = 'unfulfilled'
                             AND financial_status NOT IN ('voided', 'refunded')
                             AND cancelled_at IS NULL) AS unfulfilled_orders,
            COUNT(*) FILTER (WHERE fulfillment_status = 'fulfilled') AS fulfilled_orders,
            COUNT(*) FILTER (WHERE fulfillment_status = 'partial') AS partial_orders,
            COUNT(DISTINCT store_prefix) FILTER (WHERE financial_status NOT IN ('voided', 'refunded')) AS store_count,
            COUNT(*) FILTER (WHERE shopify_created_at::date = CURRENT_DATE
                             AND financial_status NOT IN ('voided', 'refunded')
                             AND cancelled_at IS NULL) AS today_orders,
            COUNT(*) FILTER (WHERE shopify_created_at::date = CURRENT_DATE - 1
                             AND financial_status NOT IN ('voided', 'refunded')
                             AND cancelled_at IS NULL) AS yesterday_orders
        FROM shopify_orders
    """, fetch='one') or {}

    # Last sheet sync (so the staleness banner still works)
    last_import = query(
        "SELECT imported_at FROM import_batches ORDER BY imported_at DESC LIMIT 1",
        fetch='one'
    )

    last_sync = query(
        "SELECT * FROM shopify_sync_log ORDER BY started_at DESC LIMIT 1",
        fetch='one'
    )

    # Recent unfulfilled orders — top of the daily fulfilment queue
    recent_unfulfilled = query("""
        SELECT o.id, o.name, o.order_number, o.store_prefix,
               o.shopify_created_at, o.total_price, o.currency,
               o.customer_first_name, o.customer_last_name,
               o.shipping_city, o.shipping_province,
               (SELECT COUNT(*) FROM shopify_order_items i WHERE i.order_id = o.id) AS item_count
        FROM shopify_orders o
        WHERE o.fulfillment_status = 'unfulfilled'
          AND o.financial_status NOT IN ('voided', 'refunded')
          AND o.cancelled_at IS NULL
        ORDER BY o.shopify_created_at DESC NULLS LAST
        LIMIT 10
    """)

    # Per-store unfulfilled breakdown
    store_breakdown = query("""
        SELECT store_prefix,
               COUNT(*) AS orders,
               COUNT(*) FILTER (WHERE fulfillment_status = 'unfulfilled') AS unfulfilled
        FROM shopify_orders
        WHERE financial_status NOT IN ('voided', 'refunded')
          AND cancelled_at IS NULL
        GROUP BY store_prefix
        ORDER BY unfulfilled DESC NULLS LAST, orders DESC
    """)

    # 14-day daily orders for the hero sparkline
    sparkline_rows = query("""
        WITH days AS (
            SELECT generate_series(CURRENT_DATE - 13, CURRENT_DATE, INTERVAL '1 day')::date AS d
        )
        SELECT days.d AS day,
               COALESCE(COUNT(o.id) FILTER (
                   WHERE o.financial_status NOT IN ('voided', 'refunded')
                     AND o.cancelled_at IS NULL
               ), 0) AS orders,
               COALESCE(SUM(o.total_price) FILTER (
                   WHERE o.financial_status NOT IN ('voided', 'refunded')
                     AND o.cancelled_at IS NULL
               ), 0) AS revenue
        FROM days
        LEFT JOIN shopify_orders o ON o.shopify_created_at::date = days.d
        GROUP BY days.d
        ORDER BY days.d
    """)
    sparkline = [
        {'day': r['day'], 'orders': int(r['orders'] or 0), 'revenue': float(r['revenue'] or 0)}
        for r in sparkline_rows
    ]

    stats = dict(shopify_stats)
    stats['last_import'] = last_import['imported_at'] if last_import else None

    return render_template('dashboard.html',
                           stats=stats,
                           last_sync=last_sync,
                           recent_unfulfilled=recent_unfulfilled,
                           store_breakdown=store_breakdown,
                           sparkline=sparkline,
                           today=today)
