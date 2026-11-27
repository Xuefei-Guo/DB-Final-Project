-- Create the database used by the e-commerce project.
-- IF NOT EXISTS allows this initialization script to be run repeatedly.
CREATE DATABASE IF NOT EXISTS shop
    CHARACTER SET utf8mb4;

-- Select the database for the table definitions added in later steps.
USE shop;

-- User IDs are supplied by the application or the shared dataset generator.
-- created_at follows the project's UTC timestamp convention.
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT NOT NULL,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(254) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB;

-- Category names are shared with the document store and graph database.
CREATE TABLE IF NOT EXISTS categories (
    category_id BIGINT NOT NULL,
    name VARCHAR(64) NOT NULL,
    PRIMARY KEY (category_id),
    UNIQUE KEY uq_categories_name (name)
) ENGINE=InnoDB;

-- Store the product identity, category, and return policy.
-- Category-specific attributes and variant specifications belong in MongoDB.
-- The return window starts at delivery; the fee is fixed per returned order item.
CREATE TABLE IF NOT EXISTS products (
    product_id BIGINT NOT NULL,
    category_id BIGINT NOT NULL,
    name VARCHAR(160) NOT NULL,
    return_window_days SMALLINT NOT NULL,
    restocking_fee_cents BIGINT NOT NULL,
    PRIMARY KEY (product_id),
    CONSTRAINT fk_products_category
        FOREIGN KEY (category_id) REFERENCES categories (category_id),
    CONSTRAINT chk_products_return_window
        CHECK (return_window_days >= 0),
    CONSTRAINT chk_products_restocking_fee
        CHECK (restocking_fee_cents >= 0)
) ENGINE=InnoDB;

-- Each sellable variant has its own unique SKU, current price, and stock.
-- Match variant_id with MongoDB for category-specific variant attributes.
CREATE TABLE IF NOT EXISTS product_variants (
    variant_id BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    sku VARCHAR(64) NOT NULL,
    unit_price_cents BIGINT NOT NULL,
    stock_qty INT NOT NULL,
    PRIMARY KEY (variant_id),
    UNIQUE KEY uq_product_variants_sku (sku),
    -- Support product lookups and low-stock queries.
    KEY idx_product_variants_product_variant (product_id, variant_id),
    KEY idx_product_variants_stock_qty (stock_qty),
    CONSTRAINT fk_product_variants_product
        FOREIGN KEY (product_id) REFERENCES products (product_id),
    CONSTRAINT chk_product_variants_unit_price
        CHECK (unit_price_cents >= 0),
    CONSTRAINT chk_product_variants_stock_qty
        CHECK (stock_qty >= 0)
) ENGINE=InnoDB;

-- Persist carts across devices and retain converted or abandoned cart history.
-- item_count is the sum of item quantities, maintained by cart transactions.
-- Increment version when cart contents change, not when the cart is opened.
CREATE TABLE IF NOT EXISTS carts (
    cart_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    status ENUM('ACTIVE', 'CONVERTED', 'ABANDONED') NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    last_device ENUM('tablet', 'laptop', 'phone', 'desktop') NULL,
    item_count BIGINT NOT NULL DEFAULT 0,
    version BIGINT NOT NULL DEFAULT 1,
    -- Only active carts expose user_id to the unique index; history uses NULL.
    active_user_id BIGINT GENERATED ALWAYS AS (
        CASE WHEN status = 'ACTIVE' THEN user_id ELSE NULL END
    ) VIRTUAL,
    PRIMARY KEY (cart_id),
    UNIQUE KEY uq_carts_active_user (active_user_id),
    -- Support user cart lookup, active-cart listing, and recent-cart statistics.
    KEY idx_carts_user_status (user_id, status),
    KEY idx_carts_status_cart (status, cart_id),
    KEY idx_carts_created_at (created_at),
    CONSTRAINT fk_carts_user
        FOREIGN KEY (user_id) REFERENCES users (user_id),
    CONSTRAINT chk_carts_item_count
        CHECK (item_count >= 0),
    CONSTRAINT chk_carts_version
        CHECK (version > 0)
) ENGINE=InnoDB;

-- Store one row per variant in each cart; quantity must be positive.
-- Cart transactions must delete zero-quantity rows and update the cart summary.
CREATE TABLE IF NOT EXISTS cart_items (
    cart_id BIGINT NOT NULL,
    variant_id BIGINT NOT NULL,
    quantity INT NOT NULL,
    PRIMARY KEY (cart_id, variant_id),
    CONSTRAINT fk_cart_items_cart
        FOREIGN KEY (cart_id) REFERENCES carts (cart_id),
    CONSTRAINT fk_cart_items_variant
        FOREIGN KEY (variant_id) REFERENCES product_variants (variant_id),
    CONSTRAINT chk_cart_items_quantity
        CHECK (quantity > 0)
) ENGINE=InnoDB;

-- Show enabled delivery options during checkout.
-- Estimate shipment from order placement, then delivery from shipment.
CREATE TABLE IF NOT EXISTS shipping_options (
    shipping_option_id BIGINT NOT NULL,
    name VARCHAR(64) NOT NULL,
    fee_cents BIGINT NOT NULL,
    dispatch_days SMALLINT NOT NULL,
    transit_days SMALLINT NOT NULL,
    is_enabled BOOLEAN NOT NULL,
    PRIMARY KEY (shipping_option_id),
    CONSTRAINT chk_shipping_options_fee
        CHECK (fee_cents >= 0),
    CONSTRAINT chk_shipping_options_dispatch_days
        CHECK (dispatch_days >= 0),
    CONSTRAINT chk_shipping_options_transit_days
        CHECK (transit_days >= 0),
    CONSTRAINT chk_shipping_options_is_enabled
        CHECK (is_enabled IN (0, 1))
) ENGINE=InnoDB;

-- Store one order per source cart; the cart identifies the purchasing user.
-- Preserve the delivery name and charges agreed upon during checkout.
-- All timestamps use UTC; actual shipment and delivery times may be unknown.
CREATE TABLE IF NOT EXISTS orders (
    order_id BIGINT NOT NULL,
    cart_id BIGINT NOT NULL,
    shipping_option_id BIGINT NOT NULL,
    shipping_name_snapshot VARCHAR(64) NOT NULL,
    status ENUM('CONFIRMED', 'SHIPPED', 'DELIVERED', 'CANCELLED') NOT NULL,
    placed_at DATETIME(6) NOT NULL,
    subtotal_cents BIGINT NOT NULL,
    tax_cents BIGINT NOT NULL,
    shipping_fee_cents BIGINT NOT NULL,
    total_cents BIGINT NOT NULL,
    estimated_ship_at DATETIME(6) NOT NULL,
    estimated_delivery_at DATETIME(6) NOT NULL,
    shipped_at DATETIME(6) NULL,
    delivered_at DATETIME(6) NULL,
    PRIMARY KEY (order_id),
    UNIQUE KEY uq_orders_cart (cart_id),
    CONSTRAINT fk_orders_cart
        FOREIGN KEY (cart_id) REFERENCES carts (cart_id),
    CONSTRAINT fk_orders_shipping_option
        FOREIGN KEY (shipping_option_id)
        REFERENCES shipping_options (shipping_option_id),
    CONSTRAINT chk_orders_subtotal
        CHECK (subtotal_cents >= 0),
    CONSTRAINT chk_orders_tax
        CHECK (tax_cents >= 0),
    CONSTRAINT chk_orders_shipping_fee
        CHECK (shipping_fee_cents >= 0),
    CONSTRAINT chk_orders_total
        CHECK (total_cents >= 0),
    CONSTRAINT chk_orders_total_amount
        CHECK (total_cents = subtotal_cents + tax_cents + shipping_fee_cents),
    CONSTRAINT chk_orders_estimated_delivery
        CHECK (estimated_delivery_at >= estimated_ship_at)
) ENGINE=InnoDB;

-- Preserve the product name, unit price, and return policy at checkout.
-- Store one row per variant in an order; returns refer to order_item_id.
-- Checkout must verify the order subtotal equals the sum of item amounts.
CREATE TABLE IF NOT EXISTS order_items (
    order_item_id BIGINT NOT NULL,
    order_id BIGINT NOT NULL,
    variant_id BIGINT NOT NULL,
    quantity INT NOT NULL,
    product_name_snapshot VARCHAR(160) NOT NULL,
    unit_price_cents BIGINT NOT NULL,
    return_window_days SMALLINT NOT NULL,
    restocking_fee_cents BIGINT NOT NULL,
    PRIMARY KEY (order_item_id),
    UNIQUE KEY uq_order_items_order_variant (order_id, variant_id),
    CONSTRAINT fk_order_items_order
        FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT fk_order_items_variant
        FOREIGN KEY (variant_id) REFERENCES product_variants (variant_id),
    CONSTRAINT chk_order_items_quantity
        CHECK (quantity > 0),
    CONSTRAINT chk_order_items_unit_price
        CHECK (unit_price_cents >= 0),
    CONSTRAINT chk_order_items_return_window
        CHECK (return_window_days >= 0),
    CONSTRAINT chk_order_items_restocking_fee
        CHECK (restocking_fee_cents >= 0)
) ENGINE=InnoDB;

-- Record one approved payment per order using a payment-provider token.
-- Never store full card numbers; use simulated provider tokens for this project.
-- Checkout must write the order and payment together and verify equal amounts.
CREATE TABLE IF NOT EXISTS payments (
    payment_id BIGINT NOT NULL,
    order_id BIGINT NOT NULL,
    method ENUM('CARD', 'BANK_ACCOUNT') NOT NULL,
    provider_token VARCHAR(160) NOT NULL,
    amount_cents BIGINT NOT NULL,
    approved_at DATETIME(6) NOT NULL,
    PRIMARY KEY (payment_id),
    UNIQUE KEY uq_payments_order (order_id),
    CONSTRAINT fk_payments_order
        FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT chk_payments_amount
        CHECK (amount_cents >= 0)
) ENGINE=InnoDB;

-- Track a refund or exchange request for one purchased order item.
-- This project allows at most one request per order item.
-- Return transactions must check delivery, the return window, and quantity limits.
-- Shipping details may be filled after the request is created.
CREATE TABLE IF NOT EXISTS returns (
    return_id BIGINT NOT NULL,
    order_item_id BIGINT NOT NULL,
    quantity INT NOT NULL,
    resolution ENUM('REFUND', 'EXCHANGE') NOT NULL,
    status ENUM('REQUESTED', 'IN_TRANSIT', 'RECEIVED', 'COMPLETED', 'REJECTED') NOT NULL,
    reason VARCHAR(255) NOT NULL,
    requested_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    return_shipping_method VARCHAR(24) NULL,
    return_label_ref VARCHAR(160) NULL,
    return_tracking_code VARCHAR(64) NULL,
    estimated_resolution_at DATETIME(6) NULL,
    PRIMARY KEY (return_id),
    UNIQUE KEY uq_returns_order_item (order_item_id),
    CONSTRAINT fk_returns_order_item
        FOREIGN KEY (order_item_id) REFERENCES order_items (order_item_id),
    CONSTRAINT chk_returns_quantity
        CHECK (quantity > 0)
) ENGINE=InnoDB;

-- Store the monetary outcome of a REFUND request.
-- Leave the net refund NULL until completion; zero is a valid completed amount.
-- Refund transactions must calculate the net amount and enforce the fee limit.
CREATE TABLE IF NOT EXISTS return_refunds (
    return_id BIGINT NOT NULL,
    refund_amount_cents BIGINT NULL,
    restocking_fee_cents BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (return_id),
    CONSTRAINT fk_return_refunds_return
        FOREIGN KEY (return_id) REFERENCES returns (return_id),
    CONSTRAINT chk_return_refunds_amount
        CHECK (refund_amount_cents >= 0),
    CONSTRAINT chk_return_refunds_restocking_fee
        CHECK (restocking_fee_cents >= 0)
) ENGINE=InnoDB;

-- Store the replacement variant chosen for an EXCHANGE request.
-- Exchange transactions must verify a different variant of the same product.
-- Deduct replacement stock and record shipment when completing the exchange.
CREATE TABLE IF NOT EXISTS return_exchanges (
    return_id BIGINT NOT NULL,
    exchange_variant_id BIGINT NOT NULL,
    shipped_at DATETIME(6) NULL,
    PRIMARY KEY (return_id),
    CONSTRAINT fk_return_exchanges_return
        FOREIGN KEY (return_id) REFERENCES returns (return_id),
    CONSTRAINT fk_return_exchanges_variant
        FOREIGN KEY (exchange_variant_id) REFERENCES product_variants (variant_id)
) ENGINE=InnoDB;
