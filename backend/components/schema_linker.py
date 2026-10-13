
from types import SimpleNamespace

class SchemaLinker:
    def link(self, nl_query, examples=None, db_path=None):
        # Simple keyword-based linking
        q = nl_query.lower()
        tables = []
        cols = []
        if "customer" in q or "mumbai" in q or "bangalore" in q or "city" in q:
            tables.append("customers")
        if "product" in q or "electronics" in q or "category" in q or "stock" in q:
            tables.append("products")
        if "order" in q or "status" in q or "revenue" in q or "total" in q:
            tables.append("orders")
        if "payment" in q or "paypal" in q or "method" in q:
            tables.append("payments")
        if "review" in q or "rating" in q:
            tables.append("reviews")
        if "order_item" in q or "quantity" in q:
            tables.append("order_items")
        if not tables:
            tables = ["orders"]
        # Remove dup
        tables = list(dict.fromkeys(tables))
        return SimpleNamespace(resolved_tables=tables, resolved_columns=cols, ambiguities=[])

def link_schema(nl, db_path=None):
    return SchemaLinker().link(nl, db_path=db_path)
