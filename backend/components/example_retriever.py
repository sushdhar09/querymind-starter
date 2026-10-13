
import sqlite3
from types import SimpleNamespace

from backend.config import DB_PATH

class ExampleRetriever:
    def retrieve(self, nl_query, top_k=3):
        # Return dummy examples - generator uses templates anyway
        try:
            conn = sqlite3.connect(str(DB_PATH))
            rows = conn.execute("SELECT question, sql FROM few_shot_examples LIMIT ?", (top_k,)).fetchall()
            conn.close()
            examples = [SimpleNamespace(question=r[0], sql=r[1]) for r in rows]
        except:
            examples = [
                SimpleNamespace(question="Show all customers from Mumbai", sql="SELECT * FROM customers WHERE city = 'Mumbai'"),
                SimpleNamespace(question="Count orders by status", sql="SELECT status, COUNT(*) FROM orders GROUP BY status"),
            ]
        return SimpleNamespace(examples=examples)

def retrieve_examples(nl, top_k=3):
    return ExampleRetriever().retrieve(nl, top_k)
