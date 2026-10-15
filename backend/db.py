# backend/db.py - Unified DB layer for SQLite + Azure SQL
import sqlite3
import pyodbc
from pathlib import Path
from. import config

def _get_azure_conn_str():
    # Manual read to handle { } correctly (dotenv fails on ODBC)
    env_path = config.ROOT_DIR / ".env"
    if not env_path.exists():
        return None
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("AZURE_SQL_CONNECTION_STRING="):
                val = line.split("=", 1)[1].strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                return val
    return None

def get_connection():
    db_type = getattr(config, 'DB_TYPE', 'sqlite').lower()

    if db_type == 'azuresql':
        conn_str = _get_azure_conn_str() or getattr(config, 'AZURE_SQL_CONNECTION_STRING', None)
        if not conn_str:
            raise ValueError("AZURE_SQL_CONNECTION_STRING not found in.env")
        # Use read-only user querymind_ro
        conn = pyodbc.connect(conn_str, timeout=10)
        return conn
    else:
        # SQLite fallback (local dev)
        conn = sqlite3.connect(str(config.DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn

def execute_query(sql, params=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)

        # Check if it's a SELECT
        if sql.strip().upper().startswith("SELECT"):
            rows = cur.fetchall()
            # Convert pyodbc rows to dict-like
            if isinstance(conn, pyodbc.Connection):
                columns = [col[0] for col in cur.description]
                result = [dict(zip(columns, row)) for row in rows]
            else:
                result = [dict(row) for row in rows]
            return result
        else:
            conn.commit()
            return cur.rowcount
    finally:
        conn.close()