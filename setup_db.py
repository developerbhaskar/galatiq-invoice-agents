"""Build the mock inventory DB (SQLite) the Validation agent checks against.

    python setup_db.py

FakeItem has 0 stock (out-of-stock trap). Items like WidgetC / SuperGizmo are
intentionally absent so they flag as unknown.
"""

import os
import sqlite3

from config import DB_PATH

# item -> (stock, catalog_unit_price, category)
SEED_INVENTORY = {
    "WidgetA": (15, 250.00, "widget"),
    "WidgetB": (10, 500.00, "widget"),
    "GadgetX": (5, 750.00, "gadget"),
    "FakeItem": (0, 1000.00, "flagged"),   # zero stock -> fraud/out-of-stock trap
}


def build_db(path: str = DB_PATH) -> str:
    if os.path.exists(path):
        os.remove(path)

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE inventory (
            item        TEXT PRIMARY KEY,
            stock       INTEGER NOT NULL,
            unit_price  REAL,
            category    TEXT
        )
        """
    )
    cur.executemany(
        "INSERT INTO inventory (item, stock, unit_price, category) VALUES (?, ?, ?, ?)",
        [(name, s, p, c) for name, (s, p, c) in SEED_INVENTORY.items()],
    )
    conn.commit()
    conn.close()
    return path


if __name__ == "__main__":
    p = build_db()
    print(f"[setup_db] Inventory database created at: {p}")
    conn = sqlite3.connect(p)
    for row in conn.execute("SELECT item, stock, unit_price, category FROM inventory"):
        print(f"  {row[0]:<12} stock={row[1]:<4} unit_price=${row[2]:<8} category={row[3]}")
    conn.close()
