-- Culture Circle Private Label Inventory Dashboard Schema

-- Blank types (the actual inventory items)
CREATE TABLE IF NOT EXISTS blank_master (
    blank_id        SERIAL PRIMARY KEY,
    blank_name      VARCHAR(200) NOT NULL,
    garment_type    VARCHAR(100),          -- e.g., 'Oversized T-Shirt', 'Bowling Shirt', 'Quarter Zip'
    color           VARCHAR(50),
    size            VARCHAR(20) NOT NULL,
    current_stock   NUMERIC(10,0) DEFAULT 0,
    reorder_level   NUMERIC(10,0),         -- manual override; NULL = auto-calculate
    lead_time_days  INTEGER DEFAULT 28,
    min_batch_size  INTEGER,
    safety_buffer_days INTEGER DEFAULT 7,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(blank_name, size)
);

-- Maps printed SKUs (from SourceX CSV) to blank types
CREATE TABLE IF NOT EXISTS sku_blank_mapping (
    mapping_id  SERIAL PRIMARY KEY,
    product     VARCHAR(300) NOT NULL,     -- Product name from CSV
    brand       VARCHAR(100),
    size        VARCHAR(20) NOT NULL,
    blank_id    INTEGER NOT NULL REFERENCES blank_master(blank_id),
    created_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE(product, size)
);

-- Imported order data from CSV
CREATE TABLE IF NOT EXISTS daily_orders (
    order_id        VARCHAR(50) PRIMARY KEY,  -- SX222434
    inventory_id    VARCHAR(50),
    product         VARCHAR(300) NOT NULL,
    brand           VARCHAR(100),
    size            VARCHAR(20),
    status          VARCHAR(50),
    order_date      TIMESTAMP,
    blank_id        INTEGER REFERENCES blank_master(blank_id),
    import_batch_id INTEGER REFERENCES import_batches(batch_id),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Track each CSV import
CREATE TABLE IF NOT EXISTS import_batches (
    batch_id        SERIAL PRIMARY KEY,
    file_hash       VARCHAR(64),
    file_name       VARCHAR(200),
    order_count     INTEGER DEFAULT 0,
    deducted_count  INTEGER DEFAULT 0,
    unmapped_count  INTEGER DEFAULT 0,
    imported_at     TIMESTAMP DEFAULT NOW()
);

-- Stock movements (audit trail)
CREATE TABLE IF NOT EXISTS stock_movements (
    movement_id     SERIAL PRIMARY KEY,
    blank_id        INTEGER NOT NULL REFERENCES blank_master(blank_id),
    movement_type   VARCHAR(30) NOT NULL CHECK (movement_type IN ('CSV_DEDUCTION','RESTOCK','MANUAL_ADJUSTMENT','INITIAL')),
    quantity        NUMERIC(10,0) NOT NULL,  -- negative for deductions
    balance_after   NUMERIC(10,0),
    reference_type  VARCHAR(50),             -- 'import_batch', 'restock_order', 'manual'
    reference_id    INTEGER,
    notes           TEXT,
    movement_date   DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Production/restock orders
CREATE TABLE IF NOT EXISTS restock_orders (
    restock_id          SERIAL PRIMARY KEY,
    blank_id            INTEGER NOT NULL REFERENCES blank_master(blank_id),
    qty_ordered         INTEGER NOT NULL,
    date_triggered      DATE NOT NULL DEFAULT CURRENT_DATE,
    expected_delivery   DATE,
    actual_delivery     DATE,
    status              VARCHAR(30) DEFAULT 'in_production' CHECK (status IN ('in_production','in_transit','received','cancelled')),
    notes               TEXT,
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

-- Alert log
CREATE TABLE IF NOT EXISTS alert_log (
    alert_id            SERIAL PRIMARY KEY,
    blank_id            INTEGER NOT NULL REFERENCES blank_master(blank_id),
    alert_type          VARCHAR(30) NOT NULL,  -- 'RED', 'YELLOW', 'STALENESS'
    current_stock       NUMERIC(10,0),
    avg_daily_burn      NUMERIC(10,2),
    days_to_stockout    NUMERIC(10,1),
    recommended_qty     INTEGER,
    sent_at             TIMESTAMP DEFAULT NOW()
);

-- Unmapped SKUs from CSV imports
CREATE TABLE IF NOT EXISTS unmapped_sku_log (
    id              SERIAL PRIMARY KEY,
    product         VARCHAR(300) NOT NULL,
    brand           VARCHAR(100),
    size            VARCHAR(20),
    qty             INTEGER DEFAULT 1,
    import_batch_id INTEGER REFERENCES import_batches(batch_id),
    status          VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending','mapped','skipped')),
    import_date     TIMESTAMP DEFAULT NOW()
);

-- Index for burn rate queries
CREATE INDEX IF NOT EXISTS idx_stock_movements_blank_date ON stock_movements(blank_id, movement_date);
CREATE INDEX IF NOT EXISTS idx_daily_orders_order_date ON daily_orders(order_date);
CREATE INDEX IF NOT EXISTS idx_sku_mapping_product_size ON sku_blank_mapping(product, size);
