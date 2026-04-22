-- Shopify Orders Schema — stores synced order data from all connected Shopify stores

-- Track connected Shopify stores
CREATE TABLE IF NOT EXISTS shopify_stores (
    store_id        SERIAL PRIMARY KEY,
    prefix          VARCHAR(50) UNIQUE NOT NULL,     -- e.g. 'PIEREERIC'
    store_name      VARCHAR(100) NOT NULL,
    domain          VARCHAR(200) NOT NULL,
    is_active       BOOLEAN DEFAULT TRUE,
    last_synced_at  TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Orders from all Shopify stores
CREATE TABLE IF NOT EXISTS shopify_orders (
    id                  SERIAL PRIMARY KEY,
    shopify_order_id    BIGINT NOT NULL,
    store_prefix        VARCHAR(50) NOT NULL,
    order_number        VARCHAR(50),
    name                VARCHAR(50),                  -- e.g. #1001
    email               VARCHAR(200),
    phone               VARCHAR(50),
    financial_status    VARCHAR(50),                   -- paid, pending, refunded, etc.
    fulfillment_status  VARCHAR(50) DEFAULT 'unfulfilled',  -- fulfilled, partial, unfulfilled
    total_price         NUMERIC(12,2) DEFAULT 0,
    subtotal_price      NUMERIC(12,2) DEFAULT 0,
    total_tax           NUMERIC(12,2) DEFAULT 0,
    total_discounts     NUMERIC(12,2) DEFAULT 0,
    currency            VARCHAR(10) DEFAULT 'INR',
    tags                TEXT DEFAULT '',
    note                TEXT DEFAULT '',
    cancel_reason       VARCHAR(100),
    cancelled_at        TIMESTAMP,
    closed_at           TIMESTAMP,
    -- Customer info
    customer_id         BIGINT,
    customer_first_name VARCHAR(100),
    customer_last_name  VARCHAR(100),
    customer_email      VARCHAR(200),
    customer_phone      VARCHAR(50),
    customer_orders_count INTEGER DEFAULT 0,
    customer_total_spent NUMERIC(12,2) DEFAULT 0,
    -- Shipping address
    shipping_name       VARCHAR(200),
    shipping_address1   VARCHAR(300),
    shipping_address2   VARCHAR(300),
    shipping_city       VARCHAR(100),
    shipping_province   VARCHAR(100),
    shipping_zip        VARCHAR(20),
    shipping_country    VARCHAR(100),
    shipping_phone      VARCHAR(50),
    -- Billing address
    billing_name        VARCHAR(200),
    billing_address1    VARCHAR(300),
    billing_address2    VARCHAR(300),
    billing_city        VARCHAR(100),
    billing_province    VARCHAR(100),
    billing_zip         VARCHAR(20),
    billing_country     VARCHAR(100),
    -- Timestamps from Shopify
    shopify_created_at  TIMESTAMP,
    shopify_updated_at  TIMESTAMP,
    processed_at        TIMESTAMP,
    -- Local timestamps
    synced_at           TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW(),
    UNIQUE(shopify_order_id, store_prefix)
);

-- Line items within each order
CREATE TABLE IF NOT EXISTS shopify_order_items (
    id                      SERIAL PRIMARY KEY,
    order_id                INTEGER NOT NULL REFERENCES shopify_orders(id) ON DELETE CASCADE,
    shopify_line_item_id    BIGINT NOT NULL,
    title                   VARCHAR(300),
    variant_title           VARCHAR(200),
    sku                     VARCHAR(100),
    quantity                INTEGER DEFAULT 0,
    price                   NUMERIC(12,2) DEFAULT 0,
    total_discount          NUMERIC(12,2) DEFAULT 0,
    fulfillment_status      VARCHAR(50),
    product_id              BIGINT,
    variant_id              BIGINT,
    UNIQUE(order_id, shopify_line_item_id)
);

-- Fulfillment records
CREATE TABLE IF NOT EXISTS shopify_fulfillments (
    id                      SERIAL PRIMARY KEY,
    order_id                INTEGER NOT NULL REFERENCES shopify_orders(id) ON DELETE CASCADE,
    shopify_fulfillment_id  BIGINT NOT NULL,
    status                  VARCHAR(50),
    tracking_number         VARCHAR(200),
    tracking_company        VARCHAR(100),
    tracking_url            TEXT,
    created_at              TIMESTAMP,
    UNIQUE(order_id, shopify_fulfillment_id)
);

-- Sync log for tracking sync runs
CREATE TABLE IF NOT EXISTS shopify_sync_log (
    id              SERIAL PRIMARY KEY,
    store_prefix    VARCHAR(50),
    orders_fetched  INTEGER DEFAULT 0,
    orders_new      INTEGER DEFAULT 0,
    orders_updated  INTEGER DEFAULT 0,
    errors          TEXT,
    started_at      TIMESTAMP DEFAULT NOW(),
    completed_at    TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_shopify_orders_store ON shopify_orders(store_prefix);
CREATE INDEX IF NOT EXISTS idx_shopify_orders_fulfillment ON shopify_orders(fulfillment_status);
CREATE INDEX IF NOT EXISTS idx_shopify_orders_financial ON shopify_orders(financial_status);
CREATE INDEX IF NOT EXISTS idx_shopify_orders_created ON shopify_orders(shopify_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shopify_order_items_order ON shopify_order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_shopify_fulfillments_order ON shopify_fulfillments(order_id);
