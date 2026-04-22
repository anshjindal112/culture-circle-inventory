"""
Shopify API service — fetches orders from all connected stores
and supports bidirectional status updates.
"""

import requests
from datetime import datetime

API_VERSION = "2025-01"


class ShopifyClient:
    """Lightweight Shopify REST API client for a single store."""

    def __init__(self, domain, access_token, store_name="Unknown"):
        self.domain = domain
        self.access_token = access_token
        self.store_name = store_name
        self.base_url = f"https://{domain}/admin/api/{API_VERSION}"
        self.headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }

    def _get(self, endpoint, params=None):
        url = f"{self.base_url}/{endpoint}"
        r = requests.get(url, headers=self.headers, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _put(self, endpoint, payload):
        url = f"{self.base_url}/{endpoint}"
        r = requests.put(url, headers=self.headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def _paginate(self, endpoint, resource_key, params=None):
        """Paginate through REST API results using Link header."""
        all_items = []
        url = f"{self.base_url}/{endpoint}"
        p = dict(params or {})
        p["limit"] = 250
        first = True
        while url:
            r = requests.get(
                url,
                headers=self.headers,
                params=p if first else None,
                timeout=30,
            )
            if r.status_code != 200:
                # Raise on first page so callers know the store failed
                if first:
                    r.raise_for_status()
                break
            data = r.json()
            items = data.get(resource_key, [])
            all_items.extend(items)
            # Follow pagination
            link = r.headers.get("Link", "")
            url = None
            if 'rel="next"' in link:
                for part in link.split(","):
                    if 'rel="next"' in part:
                        url = part.split("<")[1].split(">")[0]
                        break
            first = False
        return all_items

    # ── Orders ────────────────────────────────────────────────────────────

    def get_orders(self, status="any", limit=250, since_id=None, created_at_min=None):
        """Fetch orders with full details."""
        params = {"status": status, "limit": min(limit, 250)}
        if since_id:
            params["since_id"] = since_id
        if created_at_min:
            params["created_at_min"] = created_at_min
        return self._paginate("orders.json", "orders", params)

    def get_order(self, order_id):
        """Fetch a single order by ID."""
        data = self._get(f"orders/{order_id}.json")
        return data.get("order")

    def get_orders_count(self, status="any"):
        """Get total order count."""
        data = self._get("orders/count.json", {"status": status})
        return data.get("count", 0)

    def update_order(self, order_id, updates):
        """Update an order (e.g. note, tags)."""
        payload = {"order": updates}
        return self._put(f"orders/{order_id}.json", payload)

    # ── Fulfillments ──────────────────────────────────────────────────────

    def get_fulfillments(self, order_id):
        """Get fulfillments for an order."""
        data = self._get(f"orders/{order_id}/fulfillments.json")
        return data.get("fulfillments", [])

    def create_fulfillment(self, order_id, line_item_ids=None, tracking_number=None,
                           tracking_company=None, tracking_url=None):
        """Create a fulfillment for an order."""
        # First get fulfillment orders
        url = f"{self.base_url}/orders/{order_id}/fulfillment_orders.json"
        r = requests.get(url, headers=self.headers, timeout=30)
        r.raise_for_status()
        fo_data = r.json()
        fulfillment_orders = fo_data.get("fulfillment_orders", [])

        if not fulfillment_orders:
            return None

        # Build line items for fulfillment
        fo_line_items = []
        for fo in fulfillment_orders:
            if fo.get("status") in ("open", "in_progress"):
                for li in fo.get("line_items", []):
                    fo_line_items.append({
                        "fulfillment_order_id": fo["id"],
                        "fulfillment_order_line_items": [{"id": li["id"], "quantity": li["quantity"]}]
                    })

        if not fo_line_items:
            return None

        payload = {
            "fulfillment": {
                "line_items_by_fulfillment_order": fo_line_items,
            }
        }
        if tracking_number:
            payload["fulfillment"]["tracking_info"] = {
                "number": tracking_number,
                "company": tracking_company or "",
                "url": tracking_url or "",
            }

        url = f"{self.base_url}/fulfillments.json"
        r = requests.post(url, headers=self.headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json().get("fulfillment")

    def cancel_fulfillment(self, fulfillment_id):
        """Cancel a fulfillment."""
        url = f"{self.base_url}/fulfillments/{fulfillment_id}/cancel.json"
        r = requests.post(url, headers=self.headers, json={}, timeout=30)
        r.raise_for_status()
        return r.json().get("fulfillment")

    def cancel_order(self, order_id, reason=None):
        """Cancel an order on Shopify."""
        url = f"{self.base_url}/orders/{order_id}/cancel.json"
        payload = {}
        if reason:
            payload["reason"] = reason
        r = requests.post(url, headers=self.headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json().get("order")

    def close_order(self, order_id):
        """Close an order on Shopify."""
        url = f"{self.base_url}/orders/{order_id}/close.json"
        r = requests.post(url, headers=self.headers, json={}, timeout=30)
        r.raise_for_status()
        return r.json().get("order")

    def reopen_order(self, order_id):
        """Reopen a closed order."""
        url = f"{self.base_url}/orders/{order_id}/open.json"
        r = requests.post(url, headers=self.headers, json={}, timeout=30)
        r.raise_for_status()
        return r.json().get("order")


def normalize_order(order, store_name, store_prefix):
    """Convert a raw Shopify order dict into our normalized format."""
    customer = order.get("customer") or {}
    shipping = order.get("shipping_address") or {}
    billing = order.get("billing_address") or {}

    line_items = []
    for li in order.get("line_items", []):
        line_items.append({
            "shopify_line_item_id": li["id"],
            "title": li.get("title", ""),
            "variant_title": li.get("variant_title", ""),
            "sku": li.get("sku", ""),
            "quantity": li.get("quantity", 0),
            "price": li.get("price", "0.00"),
            "total_discount": li.get("total_discount", "0.00"),
            "fulfillment_status": li.get("fulfillment_status"),
            "product_id": li.get("product_id"),
            "variant_id": li.get("variant_id"),
        })

    fulfillments = []
    for f in order.get("fulfillments", []):
        fulfillments.append({
            "shopify_fulfillment_id": f["id"],
            "status": f.get("status", ""),
            "tracking_number": f.get("tracking_number", ""),
            "tracking_company": f.get("tracking_company", ""),
            "tracking_url": (f.get("tracking_urls") or [""])[0] if f.get("tracking_urls") else f.get("tracking_url", ""),
            "created_at": f.get("created_at"),
        })

    return {
        "shopify_order_id": order["id"],
        "store_prefix": store_prefix,
        "store_name": store_name,
        "order_number": order.get("order_number") or order.get("name", ""),
        "name": order.get("name", ""),
        "email": order.get("email", ""),
        "phone": order.get("phone") or customer.get("phone", ""),
        "financial_status": order.get("financial_status", ""),
        "fulfillment_status": order.get("fulfillment_status") or "unfulfilled",
        "total_price": order.get("total_price", "0.00"),
        "subtotal_price": order.get("subtotal_price", "0.00"),
        "total_tax": order.get("total_tax", "0.00"),
        "total_discounts": order.get("total_discounts", "0.00"),
        "currency": order.get("currency", "INR"),
        "tags": order.get("tags", ""),
        "note": order.get("note", ""),
        "cancel_reason": order.get("cancel_reason"),
        "cancelled_at": order.get("cancelled_at"),
        "closed_at": order.get("closed_at"),
        "created_at": order.get("created_at"),
        "updated_at": order.get("updated_at"),
        "processed_at": order.get("processed_at"),
        # Customer
        "customer_id": customer.get("id"),
        "customer_first_name": customer.get("first_name", ""),
        "customer_last_name": customer.get("last_name", ""),
        "customer_email": customer.get("email") or order.get("email", ""),
        "customer_phone": customer.get("phone") or order.get("phone", ""),
        "customer_orders_count": customer.get("orders_count", 0),
        "customer_total_spent": customer.get("total_spent", "0.00"),
        # Shipping address
        "shipping_name": shipping.get("name", ""),
        "shipping_address1": shipping.get("address1", ""),
        "shipping_address2": shipping.get("address2", ""),
        "shipping_city": shipping.get("city", ""),
        "shipping_province": shipping.get("province", ""),
        "shipping_zip": shipping.get("zip", ""),
        "shipping_country": shipping.get("country", ""),
        "shipping_phone": shipping.get("phone", ""),
        # Billing address
        "billing_name": billing.get("name", ""),
        "billing_address1": billing.get("address1", ""),
        "billing_address2": billing.get("address2", ""),
        "billing_city": billing.get("city", ""),
        "billing_province": billing.get("province", ""),
        "billing_zip": billing.get("zip", ""),
        "billing_country": billing.get("country", ""),
        # Line items & fulfillments
        "line_items": line_items,
        "fulfillments": fulfillments,
        "item_count": sum(li["quantity"] for li in line_items),
    }
