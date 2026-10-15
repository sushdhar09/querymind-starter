import sqlite3, pyodbc, os
from dotenv import load_dotenv
from pathlib import Path

# Always find files relative to project root, not where you run from
BASE_DIR = Path(__file__).parent.parent # goes from data/ to project root
SQLITE_DB = BASE_DIR / "data" / "querymind.db"

print(f"Looking for SQLite DB at: {SQLITE_DB}")
print(f"Exists? {SQLITE_DB.exists()}")

load_dotenv(BASE_DIR / ".env")

# Connect SQLite
sqlite_conn = sqlite3.connect(str(SQLITE_DB))
sqlite_conn.row_factory = sqlite3.Row

# Connect Azure (admin)
azure_conn = pyodbc.connect(os.getenv("AZURE_SQL_ADMIN_CONNECTION_STRING"), autocommit=True)
azure_cur = azure_conn.cursor()

tables_order = ["customers", "products", "orders", "order_items", "payments", "reviews"]

for table in tables_order:
    print(f"\nMigrating {table}...")
    try:
        rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
    except sqlite3.OperationalError as e:
        print(f" Skipping {table} - not in SQLite: {e}")
        continue

    if not rows:
        print(f" No rows in {table}")
        continue

    cols = rows[0].keys()
    cols_str = ", ".join(cols)
    placeholders = ", ".join(["?"] * len(cols))

    azure_cur.execute(f"DELETE FROM {table}")
    azure_cur.execute(f"SET IDENTITY_INSERT {table} ON")

    for r in rows:
        values = [r[c] for c in cols]
        azure_cur.execute(f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders})", values)

    azure_cur.execute(f"SET IDENTITY_INSERT {table} OFF")
    print(f" Migrated {len(rows)} rows")

print("\nVerifying Azure counts:")
for table in tables_order:
    try:
        azure_cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = azure_cur.fetchone()[0]
        print(f" {table}: {count}")
    except Exception as e:
        print(f" {table}: error {e}")

sqlite_conn.close()
azure_conn.close()
print("\nDONE!")