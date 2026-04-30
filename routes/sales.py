"""
Sales analytics routes — total sales by brand with date/brand filters.
"""

from flask import Blueprint, render_template, request
from db.database import query
from datetime import datetime, timedelta

sales_bp = Blueprint('sales', __name__, url_prefix='/sales')


@sales_bp.route('/')
def index():
    """Sales dashboard with brand breakdown, date and brand filters."""
    # Parse filters
    brand_filter = request.args.get('brand', '')
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    preset = request.args.get('preset', '')

    # Date presets
    today = datetime.now().date()
    if preset == 'today':
        date_from = str(today)
        date_to = str(today)
    elif preset == '7d':
        date_from = str(today - timedelta(days=6))
        date_to = str(today)
    elif preset == '30d':
        date_from = str(today - timedelta(days=29))
        date_to = str(today)
    elif preset == 'this_month':
        date_from = str(today.replace(day=1))
        date_to = str(today)
    elif preset == 'last_month':
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        date_from = str(last_month_start)
        date_to = str(last_month_end)
    elif preset == 'all':
        date_from = ''
        date_to = ''

    # Build query conditions — always exclude voided and cancelled orders
    conditions = ["o.financial_status NOT IN ('voided', 'refunded')", "o.cancelled_at IS NULL"]
    params = []

    if brand_filter:
        conditions.append("o.store_prefix = %s")
        params.append(brand_filter)
    if date_from:
        conditions.append("o.shopify_created_at::date >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("o.shopify_created_at::date <= %s")
        params.append(date_to)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Overall totals
    totals = query(f"""
        SELECT
            COUNT(*) AS total_orders,
            COALESCE(SUM(total_price), 0) AS total_sales,
            COALESCE(AVG(total_price), 0) AS avg_order_value,
            COUNT(DISTINCT store_prefix) AS brands_with_sales,
            COUNT(DISTINCT shopify_created_at::date) AS active_days
        FROM shopify_orders o
        {where}
    """, tuple(params) if params else None, fetch='one')

    # Per-brand breakdown
    brand_sales = query(f"""
        SELECT
            o.store_prefix,
            COUNT(*) AS orders,
            SUM(o.total_price) AS total_sales,
            AVG(o.total_price) AS avg_order_value,
            MIN(o.shopify_created_at::date) AS first_order,
            MAX(o.shopify_created_at::date) AS last_order,
            COUNT(DISTINCT o.shopify_created_at::date) AS active_days,
            SUM(o.total_price) / NULLIF(COUNT(DISTINCT o.shopify_created_at::date), 0) AS daily_run_rate
        FROM shopify_orders o
        {where}
        GROUP BY o.store_prefix
        ORDER BY SUM(o.total_price) DESC
    """, tuple(params) if params else None)

    # Get unique product counts per brand separately
    for b in brand_sales:
        pc_conditions = ["o.store_prefix = %s"]
        pc_params = [b['store_prefix']]
        if date_from:
            pc_conditions.append("o.shopify_created_at::date >= %s")
            pc_params.append(date_from)
        if date_to:
            pc_conditions.append("o.shopify_created_at::date <= %s")
            pc_params.append(date_to)
        pc_where = " AND ".join(pc_conditions)
        pc = query(f"""
            SELECT COUNT(DISTINCT oi.title) AS cnt
            FROM shopify_order_items oi
            JOIN shopify_orders o ON o.id = oi.order_id
            WHERE {pc_where}
        """, tuple(pc_params), fetch='one')
        b['unique_products'] = pc['cnt'] if pc else 0

    # Daily sales trend (for chart)
    daily_trend = query(f"""
        SELECT
            o.shopify_created_at::date AS sale_date,
            COUNT(*) AS orders,
            SUM(o.total_price) AS sales
        FROM shopify_orders o
        {where}
        GROUP BY o.shopify_created_at::date
        ORDER BY sale_date
    """, tuple(params) if params else None)

    # Top products across all brands
    product_conditions = []
    product_params = []
    if brand_filter:
        product_conditions.append("o.store_prefix = %s")
        product_params.append(brand_filter)
    if date_from:
        product_conditions.append("o.shopify_created_at::date >= %s")
        product_params.append(date_from)
    if date_to:
        product_conditions.append("o.shopify_created_at::date <= %s")
        product_params.append(date_to)

    product_where = f"WHERE {' AND '.join(product_conditions)}" if product_conditions else ""

    top_products = query(f"""
        SELECT
            oi.title,
            o.store_prefix,
            SUM(oi.quantity) AS units_sold,
            COUNT(DISTINCT o.id) AS orders,
            SUM(oi.quantity * oi.price) AS revenue,
            AVG(oi.price) AS avg_price
        FROM shopify_order_items oi
        JOIN shopify_orders o ON o.id = oi.order_id
        {product_where}
        GROUP BY oi.title, o.store_prefix
        ORDER BY SUM(oi.quantity * oi.price) DESC
        LIMIT 20
    """, tuple(product_params) if product_params else None)

    # Available brands for filter
    available_brands = query("SELECT DISTINCT store_prefix FROM shopify_orders ORDER BY store_prefix")

    return render_template('sales/index.html',
                           totals=totals,
                           brand_sales=brand_sales,
                           daily_trend=daily_trend,
                           top_products=top_products,
                           available_brands=available_brands,
                           brand_filter=brand_filter,
                           date_from=date_from,
                           date_to=date_to,
                           preset=preset)
