#!/usr/bin/env python3
"""
Standalone Shopify sync script — pull orders from all connected stores.

Usage:
    python3 sync_shopify.py              # sync all stores
    python3 sync_shopify.py PIEREERIC    # sync one store

For cron (every 24 hours):
    0 6 * * * cd /path/to/culture_circle_inventory && python3 sync_shopify.py >> .tmp/sync.log 2>&1
"""

import sys
import os
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask
from config import Config, get_shopify_stores
from db.database import init_app, query, execute, execute_returning, get_db
from services.shopify_service import ShopifyClient, normalize_order


def create_minimal_app():
    """Create a minimal Flask app just for DB access."""
    app = Flask(__name__)
    init_app(app)
    return app


def sync_store(store, app):
    """Sync orders from a single Shopify store."""
    client = ShopifyClient(store['domain'], store['access_token'], store['name'])

    with app.app_context():
        raw_orders = client.get_orders(status="any")
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

        return len(raw_orders), new_count, updated_count


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

    # Upsert line items and fulfillments
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


def main():
    app = create_minimal_app()
    stores = get_shopify_stores()
    store_filter = sys.argv[1] if len(sys.argv) > 1 else None

    if store_filter:
        stores = [s for s in stores if s['prefix'] == store_filter]
        if not stores:
            print(f"Store '{store_filter}' not found in .env")
            sys.exit(1)

    print(f"[{datetime.now().isoformat()}] Shopify sync starting — {len(stores)} store(s)")

    total_fetched = 0
    total_new = 0
    total_updated = 0
    errors = []

    for store in stores:
        try:
            fetched, new, updated = sync_store(store, app)
            total_fetched += fetched
            total_new += new
            total_updated += updated
            print(f"  {store['name']}: {fetched} fetched, {new} new, {updated} updated")
        except Exception as e:
            errors.append(f"{store['name']}: {e}")
            print(f"  {store['name']}: ERROR - {e}")

    # Log sync run
    with app.app_context():
        execute("""
            INSERT INTO shopify_sync_log (store_prefix, orders_fetched, orders_new, orders_updated, errors, completed_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
        """, (
            store_filter or 'ALL',
            total_fetched, total_new, total_updated,
            '; '.join(errors) if errors else None,
        ))

    print(f"[{datetime.now().isoformat()}] Done: {total_fetched} fetched, {total_new} new, {total_updated} updated"
          + (f", {len(errors)} errors" if errors else ""))


if __name__ == '__main__':
    main()
