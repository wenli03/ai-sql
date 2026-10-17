"""Generate deterministic demo seed SQL for PostgreSQL and MySQL.

Creates an e-commerce star-ish schema with Chinese sample data:
customers, products, orders, order_items, payments.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG_DIR = ROOT / "infra" / "postgres"
MY_DIR = ROOT / "infra" / "mysql"

random.seed(20261017)

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN = [
    "伟", "芳", "娜", "秀英", "敏", "静", "丽", "强", "磊", "洋", "艳", "勇",
    "军", "杰", "娟", "涛", "明", "超", "秀兰", "霞", "平", "刚", "桂英",
    "文", "辉", "鹏", "宇", "晨", "欣", "悦", "浩然", "子涵", "雨桐", "思远",
]
CITIES = [
    ("上海", "上海市"), ("北京", "北京市"), ("深圳", "广东省"), ("杭州", "浙江省"),
    ("广州", "广东省"), ("成都", "四川省"), ("南京", "江苏省"), ("武汉", "湖北省"),
    ("苏州", "江苏省"), ("西安", "陕西省"),
]
LEVELS = [("普通", 0.55), ("银卡", 0.25), ("金卡", 0.15), ("黑卡", 0.05)]
CHANNELS = [("app", 0.40), ("web", 0.25), ("miniprogram", 0.28), ("offline", 0.07)]
STATUSES = [
    ("completed", 0.68), ("paid", 0.11), ("shipped", 0.09),
    ("refunded", 0.07), ("cancelled", 0.05),
]
PAY_METHODS = [("alipay", 0.44), ("wechat", 0.41), ("card", 0.15)]
CATEGORIES = {
    "数码": ["手机", "耳机", "笔记本", "智能手表", "相机"],
    "家居": ["家具", "厨具", "床品", "收纳", "灯具"],
    "服饰": ["男装", "女装", "鞋靴", "箱包", "配饰"],
    "美妆": ["护肤", "彩妆", "香水", "洗护"],
    "食品": ["零食", "茶饮", "生鲜", "粮油"],
    "运动": ["健身器材", "运动鞋", "户外", "球类"],
    "母婴": ["奶粉", "纸尿裤", "玩具", "童装"],
    "图书": ["小说", "经管", "童书", "教育"],
}
PRODUCT_WORDS = {
    "数码": ["星耀", "极光", "声浪", "轻羽", "视界"],
    "家居": ["原木", "云朵", "暖居", "简白", "森屿"],
    "服饰": ["风迹", "城市", "轻岚", "暮色", "晨曦"],
    "美妆": ["水光", "花漾", "净透", "柔雾", "凝润"],
    "食品": ["山野", "每日", "鲜享", "原味", "轻食"],
    "运动": ["飞跃", "巅峰", "追风", "耐力", "舒展"],
    "母婴": ["小鹿", "萌宝", "安睡", "成长", "亲子"],
    "图书": ["远方", "变量", "微光", "长河", "答案"],
}


def weighted(items):
    r = random.random()
    acc = 0.0
    for value, weight in items:
        acc += weight
        if r <= acc:
            return value
    return items[-1][0]


def q(value: str) -> str:
    return value.replace("'", "''")


def rand_date(start: date, end: date) -> date:
    return start + timedelta(days=random.randint(0, (end - start).days))


def season_weight(d: date) -> float:
    # More orders around 618, double 11/12 and Chinese New Year-ish months.
    w = 1.0
    if d.month in (11, 12):
        w += 1.2
    if d.month == 6:
        w += 0.8
    if d.month in (1, 2):
        w += 0.5
    return w


def build_data():
    customers = []
    for cid in range(1, 301):
        name = random.choice(SURNAMES) + random.choice(GIVEN)
        city, province = random.choice(CITIES)
        customers.append({
            "id": cid,
            "name": name,
            "gender": weighted([("男", 0.48), ("女", 0.50), ("其他", 0.02)]),
            "age": random.randint(18, 65),
            "city": city,
            "province": province,
            "level": weighted(LEVELS),
            "register_date": rand_date(date(2024, 1, 1), date(2026, 9, 30)),
            "email": f"user{cid:03d}@example.com",
        })

    products = []
    pid = 0
    for category, subs in CATEGORIES.items():
        for _ in range(10):
            pid += 1
            sub = random.choice(subs)
            price = round(random.uniform(19, 2999), 2)
            # Price bands by category to look plausible.
            if category == "数码":
                price = round(random.uniform(199, 6999), 2)
            elif category in ("食品", "图书"):
                price = round(random.uniform(9.9, 129), 2)
            elif category in ("服饰", "美妆"):
                price = round(random.uniform(49, 899), 2)
            cost = round(price * random.uniform(0.38, 0.68), 2)
            products.append({
                "id": pid,
                "name": f"{random.choice(PRODUCT_WORDS[category])}{sub} {random.choice(['Pro','Plus','Air','经典款','轻享版','限定款'])}",
                "category": category,
                "sub_category": sub,
                "price": price,
                "cost": cost,
                "launch_date": rand_date(date(2024, 6, 1), date(2026, 8, 31)),
            })

    # Customer order propensity: a small group of loyal customers orders a lot.
    order_customers = []
    for c in customers:
        weight = 1
        if c["level"] == "金卡":
            weight = 3
        elif c["level"] == "黑卡":
            weight = 6
        elif c["level"] == "银卡":
            weight = 2
        order_customers.extend([c] * weight)

    orders = []
    items = []
    payments = []
    item_id = 0
    start, end = date(2025, 4, 1), date(2026, 9, 30)
    days = (end - start).days
    # Precompute daily weights.
    day_list = [start + timedelta(days=i) for i in range(days + 1)]
    day_weights = [season_weight(d) for d in day_list]

    for oid in range(1, 6001):
        d = random.choices(day_list, weights=day_weights, k=1)[0]
        ts = datetime(d.year, d.month, d.day, random.randint(8, 23), random.randint(0, 59), random.randint(0, 59))
        cust = random.choice(order_customers)
        status = weighted(STATUSES)
        channel = weighted(CHANNELS)
        n_items = random.choices([1, 2, 3, 4, 5], weights=[42, 30, 16, 8, 4], k=1)[0]
        chosen = random.sample(products, n_items)
        line_rows = []
        subtotal = 0.0
        for p in chosen:
            qty = random.choices([1, 2, 3], weights=[72, 20, 8], k=1)[0]
            unit = p["price"]
            discount = round(unit * qty * random.choice([0, 0, 0, 0.05, 0.1, 0.15]), 2)
            line_total = round(unit * qty - discount, 2)
            subtotal += line_total
            item_id += 1
            line_rows.append({
                "id": item_id, "order_id": oid, "product_id": p["id"],
                "quantity": qty, "unit_price": unit, "discount": discount,
            })
        total = round(subtotal, 2)
        orders.append({
            "id": oid, "customer_id": cust["id"], "order_date": ts,
            "status": status, "channel": channel, "province": cust["province"],
            "total_amount": total, "discount_amount": round(sum(r["discount"] for r in line_rows), 2),
        })
        items.extend(line_rows)
        if status != "cancelled":
            paid_at = ts + timedelta(minutes=random.randint(1, 180))
            payments.append({
                "id": oid, "order_id": oid, "pay_method": weighted(PAY_METHODS),
                "amount": total, "paid_at": paid_at, "refunded": status == "refunded",
            })

    return customers, products, orders, items, payments


def sql_value(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, datetime):
        return "'" + v.strftime("%Y-%m-%d %H:%M:%S") + "'"
    if isinstance(v, date):
        return "'" + v.isoformat() + "'"
    return "'" + q(str(v)) + "'"


def insert_block(table: str, columns: list[str], rows: list[dict], batch: int = 500) -> str:
    lines = []
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        values = ",\n".join(
            "(" + ", ".join(sql_value(r[c]) for c in columns) + ")" for r in chunk
        )
        lines.append(f"INSERT INTO {table} ({', '.join(columns)}) VALUES\n{values};")
    return "\n\n".join(lines)


def write_schema(dialect: str) -> str:
    if dialect == "postgres":
        return """CREATE TABLE customers (
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
"""
    return """CREATE TABLE customers (
  id INT PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  gender VARCHAR(10),
  age INT,
  city VARCHAR(40),
  province VARCHAR(40),
  level VARCHAR(20) NOT NULL,
  register_date DATE NOT NULL,
  email VARCHAR(120)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE products (
  id INT PRIMARY KEY,
  name VARCHAR(160) NOT NULL,
  category VARCHAR(40) NOT NULL,
  sub_category VARCHAR(40),
  price DECIMAL(12,2) NOT NULL,
  cost DECIMAL(12,2) NOT NULL,
  launch_date DATE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE orders (
  id INT PRIMARY KEY,
  customer_id INT NOT NULL,
  order_date DATETIME NOT NULL,
  status VARCHAR(20) NOT NULL,
  channel VARCHAR(20) NOT NULL,
  province VARCHAR(40) NOT NULL,
  total_amount DECIMAL(12,2) NOT NULL,
  discount_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE order_items (
  id INT PRIMARY KEY,
  order_id INT NOT NULL,
  product_id INT NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(12,2) NOT NULL,
  discount DECIMAL(12,2) NOT NULL DEFAULT 0,
  CONSTRAINT fk_items_order FOREIGN KEY (order_id) REFERENCES orders(id),
  CONSTRAINT fk_items_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE payments (
  id INT PRIMARY KEY,
  order_id INT NOT NULL,
  pay_method VARCHAR(20) NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  paid_at DATETIME NOT NULL,
  refunded TINYINT(1) NOT NULL DEFAULT 0,
  CONSTRAINT fk_payments_order FOREIGN KEY (order_id) REFERENCES orders(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_items_product ON order_items(product_id);
CREATE INDEX idx_payments_order ON payments(order_id);
"""


def write_lake(customers, products, orders, items, payments) -> None:
    """Write Parquet files and a DuckDB catalog with views over them.

    This is the local data-lake demo: the DuckDB file contains only views, and
    the actual data lives in Parquet files. Wren attaches the DuckDB file and
    queries the views, so the lineage from report -> view -> Parquet file is
    visible in the provenance panel.
    """
    import duckdb
    import pandas as pd

    lake_dir = ROOT / "infra" / "lake"
    parquet_dir = lake_dir / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)

    frames = {
        "customers": pd.DataFrame(customers),
        "products": pd.DataFrame(products),
        "orders": pd.DataFrame(orders),
        "order_items": pd.DataFrame(items),
        "payments": pd.DataFrame(payments),
    }
    for name, frame in frames.items():
        frame.to_parquet(parquet_dir / f"{name}.parquet", index=False)

    db_path = lake_dir / "lakehouse.duckdb"
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    try:
        for name in frames:
            parquet_path = str((parquet_dir / f"{name}.parquet")).replace("\\", "/")
            con.execute(
                f'CREATE OR REPLACE VIEW "{name}" AS '
                f"SELECT * FROM read_parquet('{parquet_path}')"
            )
    finally:
        con.close()
    print(f"lake written: {db_path} ({len(frames)} parquet views)")


def main():
    customers, products, orders, items, payments = build_data()
    PG_DIR.mkdir(parents=True, exist_ok=True)
    MY_DIR.mkdir(parents=True, exist_ok=True)

    for dialect, directory in (("postgres", PG_DIR), ("mysql", MY_DIR)):
        schema = write_schema(dialect)
        (directory / "01_schema.sql").write_text(schema, encoding="utf-8")

        parts = []
        if dialect == "postgres":
            parts.append("SET client_encoding = 'UTF8';")
        else:
            parts.append("SET NAMES utf8mb4;")
        parts.append("START TRANSACTION;")
        parts.append(insert_block("customers", ["id", "name", "gender", "age", "city", "province", "level", "register_date", "email"], customers))
        parts.append(insert_block("products", ["id", "name", "category", "sub_category", "price", "cost", "launch_date"], products))
        parts.append(insert_block("orders", ["id", "customer_id", "order_date", "status", "channel", "province", "total_amount", "discount_amount"], orders))
        parts.append(insert_block("order_items", ["id", "order_id", "product_id", "quantity", "unit_price", "discount"], items))
        parts.append(insert_block("payments", ["id", "order_id", "pay_method", "amount", "paid_at", "refunded"], payments))
        parts.append("COMMIT;")
        (directory / "02_data.sql").write_text("\n\n".join(parts) + "\n", encoding="utf-8")

    print(f"customers={len(customers)} products={len(products)} orders={len(orders)} items={len(items)} payments={len(payments)}")
    print(f"written: {PG_DIR} and {MY_DIR}")
    write_lake(customers, products, orders, items, payments)


if __name__ == "__main__":
    main()
