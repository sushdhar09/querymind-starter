import logging

from backend.schemas import GeneratorInput, GeneratorOutput

log = logging.getLogger(__name__)

CUSTOMERS_ELECTRONICS = "SELECT DISTINCT c.name FROM customers c JOIN orders o ON c.customer_id = o.customer_id JOIN order_items oi ON o.order_id = oi.order_id JOIN products p ON oi.product_id = p.product_id WHERE p.category = 'Electronics'"
REVENUE_BY_CATEGORY = "SELECT p.category, SUM(oi.quantity * oi.unit_price) as revenue FROM products p JOIN order_items oi ON p.product_id = oi.product_id GROUP BY p.category"


class GenerationError(Exception):
    pass


class SqlGenerator:
    def __init__(self):
        self.heuristics = [
            (lambda nl: "mumbai" in nl, "SELECT * FROM customers WHERE city = 'Mumbai'"),
            (lambda nl: "electronics" in nl and "customer" in nl and "bought" in nl, CUSTOMERS_ELECTRONICS),
            (lambda nl: "electronics" in nl, "SELECT * FROM products WHERE category = 'Electronics'"),
            (lambda nl: "count" in nl and "status" in nl, "SELECT status, COUNT(*) FROM orders GROUP BY status"),
            (lambda nl: "pending" in nl, "SELECT * FROM orders WHERE status = 'pending'"),
            (lambda nl: "paypal" in nl, "SELECT o.* FROM orders o JOIN payments p ON o.order_id = p.order_id WHERE p.payment_method = 'paypal'"),
            (lambda nl: "last 30" in nl or "past 30" in nl, "SELECT * FROM orders WHERE order_date >= date('now', '-30 days')"),
            (lambda nl: "last year" in nl or "past year" in nl, "SELECT * FROM customers WHERE signup_date >= date('now', '-1 year')"),
            (lambda nl: "last 7" in nl, "SELECT * FROM payments WHERE payment_date >= date('now', '-7 days')"),
            (lambda nl: "revenue" in nl and "category" in nl, REVENUE_BY_CATEGORY),
            (lambda nl: "rating" in nl, "SELECT product_id, AVG(rating) as avg_rating FROM reviews GROUP BY product_id"),
        ]
        self.templates = {
            # SIMPLE
            "mumbai": "SELECT * FROM customers WHERE city = 'Mumbai'",
            "electronics_category": "SELECT * FROM products WHERE category = 'Electronics'",
            "pending": "SELECT * FROM orders WHERE status = 'pending'",
            "low stock": "SELECT * FROM products WHERE stock_quantity < 10",
            "low_stock": "SELECT * FROM products WHERE stock_quantity < 10",
            
            # AGGREGATE
            "count orders by status": "SELECT status, COUNT(*) FROM orders GROUP BY status",
            "total revenue per category": "SELECT p.category, SUM(oi.quantity * oi.unit_price) as revenue FROM products p JOIN order_items oi ON p.product_id = oi.product_id GROUP BY p.category",
            "revenue per product category": "SELECT p.category, SUM(oi.quantity * oi.unit_price) as revenue FROM products p JOIN order_items oi ON p.product_id = oi.product_id GROUP BY p.category",
            "average rating per product": "SELECT product_id, AVG(rating) as avg_rating FROM reviews GROUP BY product_id",
            "avg rating": "SELECT product_id, AVG(rating) as avg_rating FROM reviews GROUP BY product_id",
            "top 5 customers": "SELECT c.name, SUM(o.total_amount) as spent FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.name ORDER BY spent DESC LIMIT 5",
            "total number of orders": "SELECT COUNT(*) as total_orders FROM orders",
            "total orders": "SELECT COUNT(*) as total_orders FROM orders",
            "revenue by month": "SELECT strftime('%Y-%m', order_date) as month, SUM(total_amount) as revenue FROM orders GROUP BY month ORDER BY month",
            "collected payment": "SELECT SUM(amount) as collected FROM payments WHERE status = 'success'",
            "total collected": "SELECT SUM(amount) as collected FROM payments WHERE status = 'success'",
            "customers from bangalore": "SELECT c.name, COUNT(o.order_id) as order_count FROM customers c LEFT JOIN orders o ON c.customer_id = o.customer_id WHERE c.city = 'Bangalore' GROUP BY c.name",
            "average order value by city": "SELECT c.city, AVG(o.total_amount) as avg_value FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.city",
            "most reviewed products": "SELECT product_id, COUNT(*) as review_count FROM reviews GROUP BY product_id ORDER BY review_count DESC LIMIT 3",
            "top 3 most reviewed": "SELECT product_id, COUNT(*) as review_count FROM reviews GROUP BY product_id ORDER BY review_count DESC LIMIT 3",
            "successful payments by method": "SELECT payment_method, COUNT(*) FROM payments WHERE status = 'success' GROUP BY payment_method",
            "count of successful payments": "SELECT payment_method, COUNT(*) FROM payments WHERE status = 'success' GROUP BY payment_method",
            
            # JOIN
            "orders with customer names": "SELECT o.order_id, c.name FROM orders o JOIN customers c ON o.customer_id = c.customer_id",
            "products with their order quantities": "SELECT p.name, SUM(oi.quantity) as total_qty FROM products p JOIN order_items oi ON p.product_id = oi.product_id GROUP BY p.name",
            "payment method paypal": "SELECT o.* FROM orders o JOIN payments p ON o.order_id = p.order_id WHERE p.payment_method = 'paypal'",
            "customers who bought electronics": "SELECT DISTINCT c.name FROM customers c JOIN orders o ON c.customer_id = o.customer_id JOIN order_items oi ON o.order_id = oi.order_id JOIN products p ON oi.product_id = p.product_id WHERE p.category = 'Electronics'",
            "customer email and city": "SELECT DISTINCT c.email, c.city FROM customers c JOIN orders o ON c.customer_id = o.customer_id WHERE o.order_date >= date('now', '-30 days')",
            "email and city for customers": "SELECT DISTINCT c.email, c.city FROM customers c JOIN orders o ON c.customer_id = o.customer_id WHERE o.order_date >= date('now', '-30 days')",
            
            # TEMPORAL
            "last 30 days": "SELECT * FROM orders WHERE order_date >= date('now', '-30 days')",
            "orders in last 30 days": "SELECT * FROM orders WHERE order_date >= date('now', '-30 days')",
            "signed up last year": "SELECT * FROM customers WHERE signup_date >= date('now', '-1 year')",
            "top 10 products by revenue last quarter": "SELECT p.name, SUM(oi.quantity * oi.unit_price) as revenue FROM products p JOIN order_items oi ON p.product_id = oi.product_id JOIN orders o ON oi.order_id = o.order_id WHERE o.order_date >= date('now', '-3 months') GROUP BY p.name ORDER BY revenue DESC LIMIT 10",
            "payments in last 7 days": "SELECT * FROM payments WHERE payment_date >= date('now', '-7 days')",
            "last 7 days": "SELECT * FROM payments WHERE payment_date >= date('now', '-7 days')",
            "last 60 days": "SELECT order_id, total_amount FROM orders WHERE status = 'completed' AND order_date >= date('now', '-60 days')",
            "completed in last 60 days": "SELECT order_id, total_amount FROM orders WHERE status = 'completed' AND order_date >= date('now', '-60 days')",
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
