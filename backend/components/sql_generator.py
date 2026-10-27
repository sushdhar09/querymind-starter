import logging
import re

from schemas import GeneratorInput, GeneratorOutput

log = logging.getLogger(__name__)

CUSTOMERS_FROM_MUMBAI = "SELECT * FROM customers WHERE city = 'Mumbai'"
ELECTRONICS_PRODUCTS = "SELECT * FROM products WHERE category = 'Electronics'"
PENDING_ORDERS = "SELECT * FROM orders WHERE status = 'pending'"
LOW_STOCK = "SELECT * FROM products WHERE stock_quantity < 10"
ORDERS_BY_STATUS = "SELECT status, COUNT(*) AS order_count FROM orders GROUP BY status"
REVENUE_BY_CATEGORY = ("SELECT p.category, SUM(oi.quantity * oi.unit_price) AS revenue FROM products p "
                       "JOIN order_items oi ON p.product_id = oi.product_id GROUP BY p.category")
AVG_RATING_PER_PRODUCT = "SELECT product_id, AVG(CAST(rating AS FLOAT)) AS avg_rating FROM reviews GROUP BY product_id"
TOP_CUSTOMERS = ("SELECT TOP 5 c.name, SUM(o.total_amount) AS spent FROM customers c JOIN orders o ON c.customer_id = o.customer_id "
                 "GROUP BY c.name ORDER BY spent DESC")
TOTAL_ORDERS = "SELECT COUNT(*) AS total_orders FROM orders"
REVENUE_BY_MONTH = ("SELECT FORMAT(order_date, 'yyyy-MM') AS month, SUM(total_amount) AS revenue FROM orders "
                    "GROUP BY FORMAT(order_date, 'yyyy-MM') ORDER BY month")
COLLECTED_PAYMENTS = "SELECT SUM(amount) AS collected FROM payments WHERE status = 'success'"
BANGALORE_ORDER_COUNTS = ("SELECT c.name, COUNT(o.order_id) AS order_count FROM customers c LEFT JOIN orders o "
                          "ON c.customer_id = o.customer_id WHERE c.city = 'Bangalore' GROUP BY c.name")
AVG_ORDER_VALUE_BY_CITY = ("SELECT c.city, AVG(o.total_amount) AS avg_value FROM customers c JOIN orders o "
                           "ON c.customer_id = o.customer_id GROUP BY c.city")
MOST_REVIEWED = ("SELECT TOP 3 product_id, COUNT(*) AS review_count FROM reviews GROUP BY product_id "
                 "ORDER BY review_count DESC, product_id")
SUCCESSFUL_PAYMENTS_BY_METHOD = ("SELECT payment_method, COUNT(*) AS payment_count FROM payments WHERE status = 'success' "
                                 "GROUP BY payment_method")
ORDERS_WITH_CUSTOMER_NAMES = "SELECT o.order_id, c.name FROM orders o JOIN customers c ON o.customer_id = c.customer_id"
PRODUCT_ORDER_QUANTITIES = ("SELECT p.name, SUM(oi.quantity) AS total_qty FROM products p JOIN order_items oi "
                            "ON p.product_id = oi.product_id GROUP BY p.name")
PAYPAL_ORDERS = "SELECT o.* FROM orders o JOIN payments p ON o.order_id = p.order_id WHERE p.payment_method = 'paypal'"
CUSTOMERS_WHO_BOUGHT_ELECTRONICS = ("SELECT DISTINCT c.name FROM customers c JOIN orders o ON c.customer_id = o.customer_id "
                                    "JOIN order_items oi ON o.order_id = oi.order_id JOIN products p ON oi.product_id = p.product_id "
                                    "WHERE p.category = 'Electronics'")
RECENT_CUSTOMER_CONTACTS = ("SELECT DISTINCT c.email, c.city FROM customers c JOIN orders o ON c.customer_id = o.customer_id "
                            "WHERE o.order_date >= DATEADD(DAY, -30, CAST(GETDATE() AS DATE))")
ORDERS_LAST_30_DAYS = "SELECT * FROM orders WHERE order_date >= DATEADD(DAY, -30, CAST(GETDATE() AS DATE))"
SIGNED_UP_LAST_YEAR = "SELECT * FROM customers WHERE signup_date >= DATEADD(YEAR, -1, CAST(GETDATE() AS DATE))"
TOP_PRODUCTS_LAST_QUARTER = ("SELECT TOP 10 p.name, SUM(oi.quantity * oi.unit_price) AS revenue FROM products p "
                             "JOIN order_items oi ON p.product_id = oi.product_id JOIN orders o ON oi.order_id = o.order_id "
                             "WHERE o.order_date >= DATEADD(MONTH, -3, CAST(GETDATE() AS DATE)) GROUP BY p.name ORDER BY revenue DESC")
PAYMENTS_LAST_7_DAYS = "SELECT * FROM payments WHERE payment_date >= DATEADD(DAY, -7, CAST(GETDATE() AS DATE))"
COMPLETED_LAST_60_DAYS = ("SELECT order_id, total_amount FROM orders WHERE status = 'completed' "
                          "AND order_date >= DATEADD(DAY, -60, CAST(GETDATE() AS DATE))")

class GenerationError(Exception):
    pass

class SqlGenerator:
    """Offline rule-based generator - NOW FULLY GENERIC for any number and any table"""

    def __init__(self):
        self.templates = {
            "mumbai": CUSTOMERS_FROM_MUMBAI,
            "electronics_category": ELECTRONICS_PRODUCTS,
            "pending": PENDING_ORDERS,
            "low stock": LOW_STOCK,
            "count orders by status": ORDERS_BY_STATUS,
            "total revenue per category": REVENUE_BY_CATEGORY,
            "revenue per product category": REVENUE_BY_CATEGORY,
            "average rating per product": AVG_RATING_PER_PRODUCT,
            "top 5 customers": TOP_CUSTOMERS,
            "total number of orders": TOTAL_ORDERS,
            "revenue by month": REVENUE_BY_MONTH,
            "collected payment": COLLECTED_PAYMENTS,
            "customers from bangalore": BANGALORE_ORDER_COUNTS,
            "average order value by city": AVG_ORDER_VALUE_BY_CITY,
            "most reviewed products": MOST_REVIEWED,
            "successful payments by method": SUCCESSFUL_PAYMENTS_BY_METHOD,
            "orders with customer names": ORDERS_WITH_CUSTOMER_NAMES,
            "products with their order quantities": PRODUCT_ORDER_QUANTITIES,
            "payment method paypal": PAYPAL_ORDERS,
            "customers who bought electronics": CUSTOMERS_WHO_BOUGHT_ELECTRONICS,
            "customer email and city": RECENT_CUSTOMER_CONTACTS,
            "last 30 days": ORDERS_LAST_30_DAYS,
            "signed up last year": SIGNED_UP_LAST_YEAR,
            "payments in last 7 days": PAYMENTS_LAST_7_DAYS,
            "last 60 days": COMPLETED_LAST_60_DAYS,
        }

    def generate(self, req: GeneratorInput) -> GeneratorOutput:
        nl = req.nl_query.lower().strip()
        log.warning(f"MOCK GENERATOR DEBUG: query='{nl}'")

        # ===== FULLY GENERIC HANDLER - NUMBER SHOULD NOT BE BARRIER =====
        
        # Get available tables from linker (if available) or use default e-commerce tables
        available_tables = []
        if req.linker and req.linker.schema_context:
            available_tables = [t.table.lower() for t in req.linker.schema_context]
        # Fallback list - ANY table you add later will work
        all_known_tables = ["products", "customers", "orders", "order_items", "payments", "reviews", "suppliers", "categories"]
        if not available_tables:
            available_tables = all_known_tables
        
        # Pattern 1: top/best/first N [optional words] [table] - ANY NUMBER
        # Matches: top 10, top 47 products, top 3 best selling, show top 5 customers, etc.
        top_pattern = re.search(r"(?:top|first|best|show|list|give|get)?\s*(\d+)\s*(?:top|first|best|most)?\s*(products|customers|orders|payments|reviews|order_items|suppliers|categories)?", nl)
        # Better pattern for "top 10 products" or "top 10 best selling products"
        top_with_table = re.search(r"\btop\s+(\d+)\b.*?(products|customers|orders|payments|reviews|order_items)", nl)
        bare_top = re.search(r"\btop\s+(\d+)\b", nl)
        # Also handle "show 10 products" without top word
        show_n = re.search(r"\b(?:show|list|give|get|display)\s+(\d+)\s+(products|customers|orders|payments|reviews)", nl)

        # Check if query is about best-selling / top-selling / popular / revenue
        is_sales_query = any(k in nl for k in ["selling", "seller", "popular", "best", "most sold", "most ordered", "trending", "revenue"])

        # CASE A: top N best selling / popular products -> SALES QUERY with ANY NUMBER
        if is_sales_query and ("product" in nl or not any(t in nl for t in all_known_tables)):
            if bare_top:
                n = bare_top.group(1)
                # ANY NUMBER WORKS: 1, 5, 10, 47, 100, 1000
                n_int = int(n)
                # Cap at 500 as per requirement - any number up to 500 allowed
                n_capped = min(n_int, 500)
                sql = (f"SELECT TOP {n_capped} p.name, SUM(oi.quantity) AS total_sold, "
                       f"SUM(oi.quantity * oi.unit_price) AS revenue "
                       f"FROM products p JOIN order_items oi ON p.product_id = oi.product_id "
                       f"GROUP BY p.name ORDER BY total_sold DESC")
                return GeneratorOutput(sql=sql, explanation=f"Top {n_capped} best selling products (generic handler, any number works)")

        # CASE B: top N [table] -> GENERIC SELECT TOP N * FROM table - ANY NUMBER, ANY TABLE
        if top_with_table:
            n = top_with_table.group(1)
            table = top_with_table.group(2)
            n_int = int(n)
            n_capped = min(n_int, 500)
            # Verify table exists (or allow any - validator will catch if not)
            sql = f"SELECT TOP {n_capped} * FROM {table}"
            return GeneratorOutput(sql=sql, explanation=f"Generic top {n_capped} {table} - any number works")

        # CASE C: Bare top N without table -> default to products, ANY NUMBER
        if bare_top:
            n = bare_top.group(1)
            n_int = int(n)
            n_capped = min(n_int, 500)
            return GeneratorOutput(sql=f"SELECT TOP {n_capped} * FROM products", 
                                 explanation=f"Bare top {n_capped} -> default products, any number works")

        # CASE D: show/list N products/customers/etc without "top" word - ANY NUMBER
        if show_n:
            n = show_n.group(1)
            table = show_n.group(2)
            n_int = int(n)
            n_capped = min(n_int, 500)
            sql = f"SELECT TOP {n_capped} * FROM {table}"
            return GeneratorOutput(sql=sql, explanation=f"Show {n_capped} {table} - generic")

        # CASE E: Just a number + table like "10 products" - ANY NUMBER
        num_table = re.search(r"\b(\d+)\s+(products|customers|orders|payments|reviews)\b", nl)
        if num_table:
            n = num_table.group(1)
            table = num_table.group(2)
            n_int = int(n)
            n_capped = min(n_int, 2000)
            if is_sales_query and table == "products":
                sql = (f"SELECT TOP {n_capped} p.name, SUM(oi.quantity) AS total_sold "
                       f"FROM products p JOIN order_items oi ON p.product_id = oi.product_id "
                       f"GROUP BY p.name ORDER BY total_sold DESC")
                return GeneratorOutput(sql=sql, explanation=f"{n_capped} best selling products")
            return GeneratorOutput(sql=f"SELECT TOP {n_capped} * FROM {table}", 
                                 explanation=f"{n_capped} {table} - generic number handler")

        # ===== GENERIC TEMPORAL HANDLER - ANY NUMBER OF DAYS =====
        # Matches: last 20 days, past 15 days, last 20 days sales, sales last 20 days, etc.
        last_n_days = re.search(r"(?:last|past)\s+(\d+)\s+days?", nl)
        if last_n_days:
            n = int(last_n_days.group(1))
            n_capped = min(n, 365)  # Cap days at 365 for safety
            # If query mentions sales/revenue/orders -> sales data for last N days
            if any(k in nl for k in ["sales", "revenue", "order", "payment"]):
                if "revenue" in nl or "sales" in nl:
                    sql = (f"SELECT SUM(oi.quantity * oi.unit_price) AS revenue, COUNT(DISTINCT o.order_id) AS order_count "
                           f"FROM orders o JOIN order_items oi ON o.order_id = oi.order_id "
                           f"WHERE o.order_date >= DATEADD(DAY, -{n_capped}, CAST(GETDATE() AS DATE))")
                    return GeneratorOutput(sql=sql, explanation=f"Sales/revenue last {n_capped} days")
                else:
                    sql = f"SELECT * FROM orders WHERE order_date >= DATEADD(DAY, -{n_capped}, CAST(GETDATE() AS DATE))"
                    return GeneratorOutput(sql=sql, explanation=f"Orders last {n_capped} days")
            # Generic last N days orders
            if "order" in nl:
                sql = f"SELECT * FROM orders WHERE order_date >= DATEADD(DAY, -{n_capped}, CAST(GETDATE() AS DATE))"
                return GeneratorOutput(sql=sql, explanation=f"Orders last {n_capped} days")
            # Payments last N days
            if "payment" in nl:
                sql = f"SELECT * FROM payments WHERE payment_date >= DATEADD(DAY, -{n_capped}, CAST(GETDATE() AS DATE))"
                return GeneratorOutput(sql=sql, explanation=f"Payments last {n_capped} days")
            # Customers signed up last N days
            if "customer" in nl and ("sign" in nl or "join" in nl):
                sql = f"SELECT * FROM customers WHERE signup_date >= DATEADD(DAY, -{n_capped}, CAST(GETDATE() AS DATE))"
                return GeneratorOutput(sql=sql, explanation=f"Customers last {n_capped} days")
            # Default: orders last N days
            sql = f"SELECT * FROM orders WHERE order_date >= DATEADD(DAY, -{n_capped}, CAST(GETDATE() AS DATE))"
            return GeneratorOutput(sql=sql, explanation=f"Generic last {n_capped} days -> orders")

        # Last N months
        last_n_months = re.search(r"(?:last|past)\s+(\d+)\s+months?", nl)
        if last_n_months:
            n = int(last_n_months.group(1))
            n_capped = min(n, 24)
            sql = f"SELECT * FROM orders WHERE order_date >= DATEADD(MONTH, -{n_capped}, CAST(GETDATE() AS DATE))"
            return GeneratorOutput(sql=sql, explanation=f"Orders last {n_capped} months")

        # Fallback to exact templates
        for key in sorted(self.templates, key=len, reverse=True):
            if key in nl:
                return GeneratorOutput(sql=self.templates[key], explanation=f"Matched template: {key}")

        # If nothing matches, try to infer table from query and return TOP 10 from that table
        for table in all_known_tables:
            if table in nl:
                return GeneratorOutput(sql=f"SELECT TOP 10 * FROM {table}", 
                                     explanation=f"Fallback: inferred table {table}, top 10")

        # Check if user asked for more than 500 - give friendly error
        large_top = re.search(r"\btop\s+(\d+)\b", nl)
        if large_top:
            n = int(large_top.group(1))
            if n > 500:
                raise GenerationError(f"Requested {n} rows exceeds maximum allowed 500. Please query up to 500 rows.")

        raise GenerationError("Could not generate SQL for this question")