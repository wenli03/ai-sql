CREATE TABLE customers (
  id INTEGER PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  gender VARCHAR(10),
  age INTEGER,
  city VARCHAR(40),
  province VARCHAR(40),
  level VARCHAR(20) NOT NULL,
  register_date DATE NOT NULL,
  email VARCHAR(120)
);

CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  name VARCHAR(160) NOT NULL,
  category VARCHAR(40) NOT NULL,
  sub_category VARCHAR(40),
  price DECIMAL(12,2) NOT NULL,
  cost DECIMAL(12,2) NOT NULL,
  launch_date DATE
);

CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  order_date TIMESTAMP NOT NULL,
  status VARCHAR(20) NOT NULL,
  channel VARCHAR(20) NOT NULL,
  province VARCHAR(40) NOT NULL,
  total_amount DECIMAL(12,2) NOT NULL,
  discount_amount DECIMAL(12,2) NOT NULL DEFAULT 0
);

CREATE TABLE order_items (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL,
  unit_price DECIMAL(12,2) NOT NULL,
  discount DECIMAL(12,2) NOT NULL DEFAULT 0
);

CREATE TABLE payments (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  pay_method VARCHAR(20) NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  paid_at TIMESTAMP NOT NULL,
  refunded BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_items_product ON order_items(product_id);
CREATE INDEX idx_payments_order ON payments(order_id);
