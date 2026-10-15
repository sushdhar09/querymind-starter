-- QueryMind AI - E-commerce Domain
-- 6 tables, 2 date fields, ambiguity: orders.total_amount vs payments.amount
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;


CREATE TABLE customers (
    customer_id INT IDENTITY(1,1) PRIMARY KEY,
    name NVARCHAR(200) NOT NULL,
    email NVARCHAR(200),
    city NVARCHAR(100),
    signup_date DATE,
    description NVARCHAR(MAX) DEFAULT 'End customer who places orders'
);

CREATE TABLE products (
    product_id INT IDENTITY(1,1) PRIMARY KEY,
    name NVARCHAR(200) NOT NULL,
    category NVARCHAR(100),
    price FLOAT,
    stock_quantity INT,
    created_at DATE
);

CREATE TABLE orders (
    order_id INT IDENTITY(1,1) PRIMARY KEY,
    customer_id INT FOREIGN KEY REFERENCES customers(customer_id),
    order_date DATE NOT NULL,
    total_amount FLOAT,
    status NVARCHAR(50),
    shipping_date DATE
);

CREATE TABLE order_items (
    item_id INT IDENTITY(1,1) PRIMARY KEY,
    order_id INT FOREIGN KEY REFERENCES orders(order_id),
    product_id INT FOREIGN KEY REFERENCES products(product_id),
    quantity INT,
    unit_price FLOAT,
    discount FLOAT
);

CREATE TABLE payments (
    payment_id INT IDENTITY(1,1) PRIMARY KEY,
    order_id INT FOREIGN KEY REFERENCES orders(order_id),
    amount FLOAT,
    payment_method NVARCHAR(50),
    payment_date DATE,
    status NVARCHAR(50)
);

CREATE TABLE reviews (
    review_id INT IDENTITY(1,1) PRIMARY KEY,
    product_id INT FOREIGN KEY REFERENCES products(product_id),
    customer_id INT FOREIGN KEY REFERENCES customers(customer_id),
    rating INT CHECK(rating BETWEEN 1 AND 5),
    review_text NVARCHAR(MAX),
    review_date DATE
);
-- For logs
CREATE TABLE query_logs (
    id INT IDENTITY(1,1) PRIMARY KEY,
    nl_query NVARCHAR(MAX),
    generated_sql NVARCHAR(MAX),
    status NVARCHAR(200),
    latency_ms INTEGER,
    row_count INTEGER,
    error TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- For admin examples
CREATE TABLE few_shot_examples (
    id INT IDENTITY(1,1) PRIMARY KEY,
    question NVARCHAR(MAX),
    sql NVARCHAR(MAX),
    query_type NVARCHAR(200),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- For schema descriptions
CREATE TABLE schema_descriptions (
    table_name NVARCHAR(400),
    column_name NVARCHAR(400),
    description NVARCHAR(MAX),
    is_sensitive BIT DEFAULT 0,
    synonyms NVARCHAR(MAX), -- comma separated
    PRIMARY KEY(table_name, column_name)
);
