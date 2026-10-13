
import sqlite3
from pathlib import Path
from types import SimpleNamespace

class ExampleRetriever:
    def retrieve(self, nl_query, top_k=3):
        # Return dummy examples - generator uses templates anyway
        try:
            db_path = Path(__file__).parent.parent.parent / "data" / "querymind.db"
            if not db_path.exists():
                db_path = Path("data/querymind.db")
            conn = sqlite3.connect(str(db_path))
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
