-- QueryMind AI - E-commerce Domain
-- 6 tables, 2 date fields, ambiguity: orders.total_amount vs payments.amount

PRAGMA foreign_keys = ON;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE,
    city TEXT,
    signup_date DATE,
    description TEXT DEFAULT 'End customer who places orders'
);

CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    price REAL,
    stock_quantity INTEGER,
    created_at DATE
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(customer_id),
    order_date DATE NOT NULL,
    total_amount REAL, -- AMBIGUITY 1: order total
    status TEXT,
    shipping_date DATE
);

CREATE TABLE order_items (
    item_id INTEGER PRIMARY KEY,
    order_id INTEGER REFERENCES orders(order_id),
    product_id INTEGER REFERENCES products(product_id),
    quantity INTEGER,
    unit_price REAL,
    discount REAL
);

CREATE TABLE payments (
    payment_id INTEGER PRIMARY KEY,
    order_id INTEGER REFERENCES orders(order_id),
    amount REAL, -- AMBIGUITY 2: payment amount (may differ due to refunds)
    payment_method TEXT,
    payment_date DATE,
    status TEXT
);

CREATE TABLE reviews (
    review_id INTEGER PRIMARY KEY,
    product_id INTEGER REFERENCES products(product_id),
    customer_id INTEGER REFERENCES customers(customer_id),
    rating INTEGER CHECK(rating BETWEEN 1 AND 5),
    review_text TEXT,
    review_date DATE
);

-- For logs
CREATE TABLE query_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nl_query TEXT,
    generated_sql TEXT,
    status TEXT,
    latency_ms INTEGER,
    row_count INTEGER,
    error TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- For admin examples
CREATE TABLE few_shot_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT,
    sql TEXT,
    query_type TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- For schema descriptions
CREATE TABLE schema_descriptions (
    table_name TEXT,
    column_name TEXT,
    description TEXT,
    is_sensitive BOOLEAN DEFAULT 0,
    synonyms TEXT, -- comma separated
    PRIMARY KEY(table_name, column_name)
);
