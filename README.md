# Culture Circle Inventory Management System

Private-label blank inventory tracker with Shopify order integration. Tracks blank garment stock across 16+ garment types, auto-deducts inventory from SourceX order CSVs, syncs orders from all connected Shopify stores with bidirectional status updates, and manages restock orders.

## Features

### Inventory Dashboard (`/`)
- Stock health overview with burn rate calculations (red/yellow/green/gray)
- Stock breakdown by garment type with clickable cards
- Attention panel for items needing restock
- Inventory snapshot with health bar

### Shopify Orders (`/orders`)
- Fetches orders from all connected Shopify stores
- Full order details: customer info, addresses, line items, pricing, fulfillment status
- Bidirectional sync — fulfill, cancel, close, reopen orders from the dashboard and it updates Shopify
- Filter by store, payment status, fulfillment status, search by name/email/phone
- Store connection status panel showing which stores are authorized

### Blank Inventory (`/blanks`)
- Grid view with size matrix (XS-XXL) per garment type and color, color-coded stock cells
- Table view for detailed editing
- Filter by garment type, stock status (in stock / out of stock)
- Stock bar chart overview across all types

### SKU Mappings (`/blanks/mappings`)
- Grouped by blank type with expandable product lists
- Search across product names and blank names
- Filter by garment type

### CSV Import (`/csv/upload`)
- Upload SourceX order CSVs — auto-maps SKUs to blanks, deducts stock
- Unmapped SKU queue with auto-mapper suggestions
- Import history with duplicate detection

### Restock Orders (`/restock`)
- Create and track production/restock orders
- Receiving restocks auto-adds stock back with audit trail

### Shopify Sync (`sync_shopify.py`)
- Standalone script for cron-based order syncing
- Syncs all stores or a single store

## Tech Stack

- **Backend**: Python 3.9+ / Flask 3.0 / Gunicorn
- **Database**: PostgreSQL
- **Frontend**: Jinja2 templates, Bootstrap 5, Bootstrap Icons
- **APIs**: Shopify Admin REST API (orders, fulfillments)

## Setup

### 1. Clone and install
```bash
git clone <repo-url>
cd culture_circle_inventory
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your database credentials and Shopify tokens
```

### 3. Initialize database
```bash
python db/init_db.py
```

### 4. Run
```bash
# Development
python app.py

# Production
gunicorn app:app --bind 0.0.0.0:5002 --workers 2
```

Open **http://localhost:5002**

## Project Structure

```
app.py                  Flask app entry point (port 5002)
config.py               Env-based config + Shopify store registry
sync_shopify.py         Standalone Shopify order sync (cron-ready)
daily_import.py         Google Sheets auto-import (cron-ready)
Procfile                Gunicorn production config
requirements.txt        Python dependencies

db/
  schema.sql            Core inventory schema (8 tables)
  schema_orders.sql     Shopify orders schema (5 tables)
  database.py           DB connection + query/execute helpers
  init_db.py            Creates database + runs all schemas

routes/
  dashboard.py          Dashboard with burn rate + stock overview
  orders.py             Shopify orders with bidirectional sync
  csv_import.py         CSV upload, order processing, unmapped queue
  blanks.py             Blank CRUD, SKU mappings, bulk edit
  restock.py            Restock order management

services/
  shopify_service.py    Shopify REST API client (orders, fulfillments)
  auto_mapper.py        Keyword-based SKU-to-blank mapping

templates/              Jinja2 HTML (sidebar layout, modern UI)
static/                 CSS assets
```

## Cron Jobs

```bash
# Sync Shopify orders daily at 6 AM
0 6 * * * cd /path/to/culture_circle_inventory && python sync_shopify.py >> .tmp/sync.log 2>&1

# Import from Google Sheets daily at 9 AM
0 9 * * * cd /path/to/culture_circle_inventory && python daily_import.py >> .tmp/import.log 2>&1
```
