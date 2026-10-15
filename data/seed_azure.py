"""Create and fill the demo e-commerce tables in Azure SQL (only needed for a brand-new database).

    python data/seed_azure.py --write-schema     regenerate data/schema.sql from backend/demo_data.py (no database needed)
    python data/seed_azure.py --yes              create the tables and load the deterministic demo rows
    python data/seed_azure.py --yes --recreate   DROP the demo tables first (destructive; for rebuilding a demo database)

It uses AZURE_SQL_ADMIN_CONNECTION_STRING (a login that may create tables), never the app's read-only login. By default it
refuses to touch a database where any demo table already exists. Everything runs in one transaction.
"""
import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import demo_data  # noqa: E402

SCHEMA_FILE = ROOT / "data" / "schema.sql"
BATCH_SIZE = 1000
ENV_VAR = "AZURE_SQL_ADMIN_CONNECTION_STRING"


def _existing_tables(cur) -> dict[str, int]:
    names = ", ".join(f"'{n}'" for n in demo_data.TABLE_NAMES)
    cur.execute("SELECT t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
                f"WHERE s.name = 'dbo' AND t.name IN ({names})")
    found = [r[0] for r in cur.fetchall()]
    counts = {}
    for table in found:
        cur.execute(f"SELECT COUNT_BIG(*) FROM dbo.[{table}]")
        counts[table] = int(cur.fetchone()[0])
    return counts


def seed(conn, rows: dict[str, list[tuple]], recreate: bool, out: Callable[[str], None] = print) -> int:
    cur = conn.cursor()
    existing = _existing_tables(cur)
    if existing and not recreate:
        listing = ", ".join(f"{t} ({n} rows)" for t, n in existing.items())
        out(f"Refusing to seed: these demo tables already exist: {listing}. Nothing was changed. "
            "Use --recreate only if you really want them dropped and rebuilt.")
        return 1
    statements = demo_data.tsql_statements()
    for statement in statements:
        if statement.startswith("DROP") and not recreate:
            continue
        cur.execute(statement)
    cur.fast_executemany = True
    for table in demo_data.SCHEMA:
        columns = ", ".join(c.name for c in table.columns)
        sql = f"INSERT INTO dbo.{table.name} ({columns}) VALUES ({', '.join('?' * len(table.columns))})"
        data = rows[table.name]
        for start in range(0, len(data), BATCH_SIZE):
            cur.executemany(sql, data[start:start + BATCH_SIZE])
        out(f"  {table.name}: {len(data)} rows")
    conn.commit()
    return 0


def main(argv: Optional[list[str]] = None, connect: Optional[Callable] = None, env: Optional[dict] = None,
         out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write-schema", action="store_true", help="write data/schema.sql and exit")
    parser.add_argument("--yes", action="store_true", help="actually create and fill the tables")
    parser.add_argument("--recreate", action="store_true", help="drop existing demo tables first (destructive)")
    args = parser.parse_args(argv)

    if args.write_schema:
        SCHEMA_FILE.write_text(demo_data.tsql_ddl(), encoding="utf-8", newline="\n")
        out(f"Wrote {SCHEMA_FILE}")
        return 0
    if not args.yes:
        out("Dry run: would create the demo tables and load "
            f"{sum(demo_data.ROW_COUNTS.values())} rows into Azure SQL. Re-run with --yes to do it.")
        return 2
    env = os.environ if env is None else env
    if env is os.environ:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    connection_string = env.get(ENV_VAR, "")
    if not connection_string:
        out(f"{ENV_VAR} is not set. Put an admin connection string in .env (never commit it).")
        return 1
    if connect is None:
        import pyodbc
        connect = pyodbc.connect
    conn = connect(connection_string, timeout=30)
    try:
        code = seed(conn, demo_data.generate(today=date.today()), args.recreate, out)
        if code:
            conn.rollback()
        return code
    except Exception as e:
        conn.rollback()
        out(f"Seeding failed and was rolled back: {type(e).__name__}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
