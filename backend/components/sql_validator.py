from backend.schemas import ValidatorOutput

BLOCKED = ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER", "CREATE", "REPLACE"]


class SqlValidator:
    def validate(self, sql: str, max_rows: int = 100) -> ValidatorOutput:
        sanitized = sql.strip().rstrip(";")
        upper = sanitized.upper()
        for b in BLOCKED:
            if upper.startswith(b):
                return ValidatorOutput(valid=False, sanitized_sql=sql, errors=[f"Blocked {b}"])
        if not upper.startswith(("SELECT", "WITH")):
            return ValidatorOutput(valid=False, sanitized_sql=sql, errors=["Only SELECT statements are allowed"])
        if "LIMIT" not in upper:
            sanitized = f"{sanitized} LIMIT {max_rows}"
        return ValidatorOutput(valid=True, sanitized_sql=sanitized)
