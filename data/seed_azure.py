import pyodbc, os
from dotenv import load_dotenv
load_dotenv()

conn = pyodbc.connect(os.getenv("AZURE_SQL_CONNECTION_STRING"), autocommit=True)
cur = conn.cursor()

print("Creating tables...")
with open("schema.sql", "r", encoding="utf-8") as f:
    sql = f.read()

cur.execute(sql)
# Consume all result sets
try:
    while cur.nextset():
        pass
except:
    pass

print("Done! Checking tables...")
cur.execute("SELECT name FROM sys.tables ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print(f"Tables in Azure: {tables}")

conn.close()