"""
migrate_db.py — Run this ONCE to upgrade your existing budget.db
Usage: python migrate_db.py
"""
import sqlite3, os

DB_PATH = "budget.db"
if not os.path.exists(DB_PATH):
    print(f"❌ {DB_PATH} not found. Run this from your project folder.")
    exit(1)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

def get_columns(table):
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}

def table_exists(table):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return bool(cur.fetchone())

def add_column(table, column, col_type):
    if not table_exists(table):
        print(f"  ⚠️  Table '{table}' missing, skipping '{column}'"); return
    if column in get_columns(table):
        print(f"  ✓  {table}.{column}"); return
    try:
        cur.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {col_type}')
        print(f"  ✅ Added {table}.{column}")
    except Exception as e:
        print(f"  ❌ {table}.{column}: {e}")

print("🔧 Migrating database...\n")

# Existing table upgrades
add_column("credit_card",    "interest_rate", "REAL DEFAULT 0.0")
add_column("credit_card",    "notes",         "TEXT")
add_column("wallet",         "currency",      "VARCHAR(10) DEFAULT 'LKR'")
add_column("wallet",         "color",         "VARCHAR(20) DEFAULT '#6366f1'")
add_column("wallet",         "notes",         "TEXT")
add_column("transaction",    "category",      "VARCHAR(100) DEFAULT 'General'")
add_column("transaction",    "notes",         "TEXT")
add_column("transaction",    "tags",          "VARCHAR(200)")
add_column("fixed_expense",  "notes",         "TEXT")
add_column("wallet_transfer","note",          "VARCHAR(200)")

# New tables
new_tables = {
    "goal": """CREATE TABLE IF NOT EXISTS goal (
        id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, description TEXT,
        target_amount REAL NOT NULL, current_amount REAL DEFAULT 0.0,
        target_date DATE, category VARCHAR(50) DEFAULT 'Savings',
        status VARCHAR(20) DEFAULT 'active', wallet_id INTEGER REFERENCES wallet(id),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        icon VARCHAR(10) DEFAULT '🎯', color VARCHAR(20) DEFAULT '#6366f1')""",
    "notification": """CREATE TABLE IF NOT EXISTS notification (
        id INTEGER PRIMARY KEY, title VARCHAR(200) NOT NULL, message TEXT NOT NULL,
        notif_type VARCHAR(30) DEFAULT 'info', is_read BOOLEAN DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        related_type VARCHAR(50), related_id INTEGER)""",
    "recurring_payment": """CREATE TABLE IF NOT EXISTS recurring_payment (
        id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, amount REAL NOT NULL,
        frequency VARCHAR(20) NOT NULL, next_date DATE NOT NULL, end_date DATE,
        category VARCHAR(100) DEFAULT 'General',
        wallet_id INTEGER REFERENCES wallet(id),
        credit_card_id INTEGER REFERENCES credit_card(id),
        is_active BOOLEAN DEFAULT 1, auto_apply BOOLEAN DEFAULT 0, last_applied DATE)""",
    "app_settings": """CREATE TABLE IF NOT EXISTS app_settings (
        id INTEGER PRIMARY KEY, key VARCHAR(100) UNIQUE NOT NULL, value TEXT,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)""",
    "budget_planner": """CREATE TABLE IF NOT EXISTS budget_planner (
        id INTEGER PRIMARY KEY, category VARCHAR(100) NOT NULL,
        amount REAL NOT NULL, month VARCHAR(7) NOT NULL)""",
}

print()
for name, sql in new_tables.items():
    if not table_exists(name):
        cur.execute(sql); print(f"  ✅ Created table: {name}")
    else:
        print(f"  ✓  Table exists: {name}")

conn.commit(); conn.close()
print("\n✅ Done! Run: python app.py")
