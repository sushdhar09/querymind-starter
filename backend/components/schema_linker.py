from backend.schemas import LinkerOutput

KEYWORDS = {
    "customers": ["customer", "mumbai", "bangalore", "city"],
    "products": ["product", "electronics", "category", "stock"],
    "orders": ["order", "status", "revenue", "total"],
    "payments": ["payment", "paypal", "method"],
    "reviews": ["review", "rating"],
    "order_items": ["order_item", "quantity"],
}


class SchemaLinker:
    def link(self, nl_query: str) -> LinkerOutput:
        q = nl_query.lower()
        tables = [t for t, words in KEYWORDS.items() if any(w in q for w in words)]
        return LinkerOutput(resolved_tables=tables or ["orders"])
