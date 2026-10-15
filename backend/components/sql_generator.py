import logging

from backend.schemas import GeneratorInput, GeneratorOutput

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
    """Offline rule-based generator (the `mock` provider). It only knows the phrasings below; use a real LLM for anything else."""

    def __init__(self):
        self.heuristics = [
            (lambda nl: "mumbai" in nl, CUSTOMERS_FROM_MUMBAI),
            (lambda nl: "electronics" in nl and "customer" in nl and "bought" in nl, CUSTOMERS_WHO_BOUGHT_ELECTRONICS),
            (lambda nl: "electronics" in nl, ELECTRONICS_PRODUCTS),
            (lambda nl: "count" in nl and "status" in nl, ORDERS_BY_STATUS),
            (lambda nl: "pending" in nl, PENDING_ORDERS),
            (lambda nl: "paypal" in nl, PAYPAL_ORDERS),
            (lambda nl: "last 30" in nl or "past 30" in nl, ORDERS_LAST_30_DAYS),
            (lambda nl: "last year" in nl or "past year" in nl, SIGNED_UP_LAST_YEAR),
            (lambda nl: "last 7" in nl, PAYMENTS_LAST_7_DAYS),
            (lambda nl: "revenue" in nl and "category" in nl, REVENUE_BY_CATEGORY),
            (lambda nl: "rating" in nl, AVG_RATING_PER_PRODUCT),
        ]
        self.templates = {
            "mumbai": CUSTOMERS_FROM_MUMBAI,
            "electronics_category": ELECTRONICS_PRODUCTS,
            "pending": PENDING_ORDERS,
            "low stock": LOW_STOCK,
            "low_stock": LOW_STOCK,
            "count orders by status": ORDERS_BY_STATUS,
            "total revenue per category": REVENUE_BY_CATEGORY,
            "revenue per product category": REVENUE_BY_CATEGORY,
            "average rating per product": AVG_RATING_PER_PRODUCT,
            "avg rating": AVG_RATING_PER_PRODUCT,
            "top 5 customers": TOP_CUSTOMERS,
            "total number of orders": TOTAL_ORDERS,
            "total orders": TOTAL_ORDERS,
            "revenue by month": REVENUE_BY_MONTH,
            "collected payment": COLLECTED_PAYMENTS,
            "total collected": COLLECTED_PAYMENTS,
            "customers from bangalore": BANGALORE_ORDER_COUNTS,
            "average order value by city": AVG_ORDER_VALUE_BY_CITY,
            "most reviewed products": MOST_REVIEWED,
            "top 3 most reviewed": MOST_REVIEWED,
            "successful payments by method": SUCCESSFUL_PAYMENTS_BY_METHOD,
            "count of successful payments": SUCCESSFUL_PAYMENTS_BY_METHOD,
            "orders with customer names": ORDERS_WITH_CUSTOMER_NAMES,
            "products with their order quantities": PRODUCT_ORDER_QUANTITIES,
            "payment method paypal": PAYPAL_ORDERS,
            "customers who bought electronics": CUSTOMERS_WHO_BOUGHT_ELECTRONICS,
            "customer email and city": RECENT_CUSTOMER_CONTACTS,
            "email and city for customers": RECENT_CUSTOMER_CONTACTS,
            "last 30 days": ORDERS_LAST_30_DAYS,
            "orders in last 30 days": ORDERS_LAST_30_DAYS,
            "signed up last year": SIGNED_UP_LAST_YEAR,
            "top 10 products by revenue last quarter": TOP_PRODUCTS_LAST_QUARTER,
            "payments in last 7 days": PAYMENTS_LAST_7_DAYS,
            "last 7 days": PAYMENTS_LAST_7_DAYS,
            "last 60 days": COMPLETED_LAST_60_DAYS,
            "completed in last 60 days": COMPLETED_LAST_60_DAYS,
        }

    def generate(self, req: GeneratorInput) -> GeneratorOutput:
        nl = req.nl_query.lower()
        for key in sorted(self.templates, key=len, reverse=True):
            if key in nl:
                return GeneratorOutput(sql=self.templates[key], explanation=f"Matched template: {key}")
        for cond, sql in self.heuristics:
            if cond(nl):
                return GeneratorOutput(sql=sql, explanation="Heuristic match")
        raise GenerationError("Could not generate SQL for this question")
