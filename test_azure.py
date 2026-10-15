import pymssql
conn = pymssql.connect(
    server='querymind-mysql-2026.database.windows.net',
    user='querymind_ro',
    password='use_ro@2026',
    database='querymind-db'
)
cur = conn.cursor()
cur.execute("SELECT TOP 5 name FROM sys.tables")
print(cur.fetchall())