
from types import SimpleNamespace

BLOCKED = ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER", "CREATE", "REPLACE"]

class SqlValidator:
    def validate(self, sql: str, max_rows=100):
        upper = sql.upper()
        for b in BLOCKED:
            if b in upper and "SELECT" not in upper.split(b)[0][-20:]:  # crude check
                # Allow if it's inside string? Simple block
                if b in ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER"]:
                    # Check if query starts with blocked keyword or contains it as statement
                    if upper.strip().startswith(b) or f" {b} " in upper:
                        # If it's SELECT with these words inside, allow - but block real destructive
                        if upper.strip().startswith(b):
                            return SimpleNamespace(is_valid=False, errors=[f"Blocked {b}"], sanitized_sql=sql)
        # Add LIMIT if missing and it's SELECT
        sanitized = sql.strip().rstrip(";")
        if sanitized.upper().startswith("SELECT") and "LIMIT" not in sanitized.upper():
            sanitized = f"{sanitized} LIMIT {max_rows}"
        return SimpleNamespace(is_valid=True, errors=[], sanitized_sql=sanitized)

def validate_sql(sql, max_rows=100):
    return SqlValidator().validate(sql, max_rows)
