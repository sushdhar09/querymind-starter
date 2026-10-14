
import os
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
_env_db = os.environ.get("DB_PATH")
DB_PATH = (Path(_env_db) if Path(_env_db).is_absolute() else BASE_DIR.parent / _env_db) if _env_db else BASE_DIR / "querymind.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
SCHEMA_PATH = BASE_DIR / "schema.sql"
if os.path.exists(DB_PATH): 
    os.remove(DB_PATH)

conn = sqlite3.connect(DB_PATH)
with open(SCHEMA_PATH) as f:
    conn.executescript(f.read())

random.seed(42)

first_names = ["Aarav","Siya","Rohan","Ananya","Vikram","Priya","Arjun","Neha","Suresh","Kiran","Meera","John","Sarah","David","Emma","Raj","Pooja","Amit","Lisa","Mike"]
last_names = ["Sharma","Patel","Singh","Kumar","Gupta","Reddy","Nair","Shah","Verma","Mehta","Smith","Jones","Brown","Davis","Wilson"]
cities = ["Mumbai","Delhi","Bangalore","Hyderabad","Chennai","Pune","Kolkata","Jaipur","Ahmedabad","Surat","New York","London"]
cats = ['Electronics','Apparel','Home','Sports','Books']
product_base = ["Widget","Gadget","Tool","Device","Gear","Kit","Pack","Box"]
suffixes = ["Pro","Max","Lite","Ultra","Plus","Mini"]
methods = ['credit_card','paypal','upi','net_banking']
statuses = ['completed','pending','cancelled']
pay_status = ['success','failed','refunded']

def rand_date(days_back=365):
    return date.today() - timedelta(days=random.randint(0, days_back))

def rand_name():
    return f"{random.choice(first_names)} {random.choice(last_names)}"

def rand_email(name, idx):
    return f"{name.lower().replace(' ','.')}{idx}@example.com"

# customers 1000
customers = []
for i in range(1, 1001):
    name = rand_name()
    customers.append((i, name, rand_email(name, i), random.choice(cities), rand_date(730)))
conn.executemany("INSERT INTO customers(customer_id, name, email, city, signup_date) VALUES (?,?,?,?,?)", customers)

# products 200
products=[]
for i in range(1, 201):
    pname = f"{random.choice(product_base)} {random.choice(suffixes)} {i}"
    products.append((i, pname, random.choice(cats), round(random.uniform(10,1000),2), random.randint(0,500), rand_date(365)))
conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", products)

# orders 5000
orders=[]
for i in range(1, 5001):
    cust = random.randint(1,1000)
    o_date = rand_date(365)
    s_date = o_date + timedelta(days=random.randint(1,7))
    orders.append((i, cust, o_date, round(random.uniform(20,2000),2), random.choice(statuses), s_date))
conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", orders)

# order_items 15000
items=[]
# cache prices
price_map = {pid: price for pid, price in conn.execute("SELECT product_id, price FROM products").fetchall()}
for i in range(1,15001):
    oid = random.randint(1,5000)
    pid = random.randint(1,200)
    qty = random.randint(1,5)
    price = price_map.get(pid, 100.0)
    items.append((i, oid, pid, qty, price, round(random.uniform(0,0.2),2)))
conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)

# payments 5000
pays=[]
for i in range(1,5001):
    oid = i
    amt_row = conn.execute("SELECT total_amount FROM orders WHERE order_id=?", (oid,)).fetchone()
    amt = amt_row[0] if amt_row else round(random.uniform(20,2000),2)
    if random.random()<0.1:
        amt = round(amt*0.9,2)
    pays.append((i, oid, amt, random.choice(methods), rand_date(365), random.choice(pay_status)))
conn.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?)", pays)

# reviews 3000
revs=[]
for i in range(1,3001):
    revs.append((i, random.randint(1,200), random.randint(1,1000), random.randint(1,5), f"This product is {random.choice(['great','good','average','excellent','poor'])}.", rand_date(365)))
conn.executemany("INSERT INTO reviews VALUES (?,?,?,?,?,?)", revs)

# schema descriptions
descs = [
    ('orders','total_amount','Total value of the order as calculated at checkout. Synonyms: revenue, order value, sales','0','revenue,sales,order value'),
    ('payments','amount','Actual amount captured via payment gateway. May differ from orders.total_amount due to refunds. Synonyms: revenue, payment','0','revenue,payment,collected'),
    ('customers','name','Full name of customer','0','customer,client,user'),
    ('products','name','Product display name','0','product,item,sku'),
    ('orders','order_date','Date when order was placed','0','order date,purchase date'),
    ('customers','signup_date','Customer registration date','0','signup,registration'),
]
conn.executemany("INSERT INTO schema_descriptions VALUES (?,?,?,?,?)", descs)

examples = [
    ("Show all customers from Mumbai", "SELECT * FROM customers WHERE city = 'Mumbai'", "SELECT_SIMPLE"),
    ("What were top 10 products by revenue last quarter?", "SELECT p.name, SUM(oi.quantity * oi.unit_price) as revenue FROM products p JOIN order_items oi ON p.product_id = oi.product_id JOIN orders o ON oi.order_id = o.order_id WHERE o.order_date >= date('now','-3 months') GROUP BY p.name ORDER BY revenue DESC LIMIT 10", "SELECT_AGGREGATE"),
    ("Count orders by status", "SELECT status, COUNT(*) FROM orders GROUP BY status", "SELECT_AGGREGATE"),
    ("List orders with customer names", "SELECT o.order_id, c.name FROM orders o JOIN customers c ON o.customer_id = c.customer_id", "SELECT_JOIN"),
    ("Orders in last 30 days", "SELECT * FROM orders WHERE order_date >= date('now','-30 days')", "SELECT_TEMPORAL"),
    ("Average rating per product", "SELECT product_id, AVG(rating) FROM reviews GROUP BY product_id", "SELECT_AGGREGATE"),
    ("Total revenue per category", "SELECT p.category, SUM(oi.quantity * oi.unit_price) as revenue FROM products p JOIN order_items oi ON p.product_id = oi.product_id GROUP BY p.category", "SELECT_AGGREGATE"),
    ("Customers who signed up last year", "SELECT * FROM customers WHERE signup_date >= date('now','-1 year')", "SELECT_TEMPORAL"),
    ("Top 5 customers by total amount spent", "SELECT c.name, SUM(o.total_amount) as spent FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.name ORDER BY spent DESC LIMIT 5", "SELECT_AGGREGATE"),
    ("Orders with payment method paypal", "SELECT o.* FROM orders o JOIN payments p ON o.order_id = p.order_id WHERE p.payment_method='paypal'", "SELECT_JOIN"),
]
while len(examples)<30:
    examples.extend(examples[:10])
examples = examples[:30]
conn.executemany("INSERT INTO few_shot_examples(question, sql, query_type) VALUES (?,?,?)", examples)

conn.commit()
conn.close()
print(f"DB seeded at {DB_PATH} with 1000 customers, 5000 orders, etc. - NO faker needed")
