"""
migrate_to_postgres.py
Copies all data from SQLite (instance/budget.db) to PostgreSQL.
Run ONCE after setting DATABASE_URL in your .env file.

Usage:
    python migrate_to_postgres.py
"""
import os, sqlite3
from dotenv import load_dotenv
load_dotenv(override=True)

# Verify correct DB is being used
db_url = os.getenv('DATABASE_URL', '')
if 'supabase' not in db_url and 'postgresql' not in db_url:
    print(f"❌ DATABASE_URL doesn't look like PostgreSQL: {db_url[:50]}")
    print("Check your .env file!")
    exit(1)
print(f"✅ Using: {db_url[:50]}...")

DATABASE_URL = os.getenv('DATABASE_URL', '')
if not DATABASE_URL:
    print("❌ DATABASE_URL not found in .env")
    exit(1)

if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

print(f"📦 Source: instance/budget.db")
print(f"🐘 Target: PostgreSQL ({DATABASE_URL[:40]}...)\n")

# Step 1: Create all tables in PostgreSQL using Flask app
print("Step 1 — Creating tables in PostgreSQL...")
from app import app, db
with app.app_context():
    db.create_all()
    print("✅ All tables created\n")

# Step 2: Read all data from SQLite
print("Step 2 — Reading data from SQLite...")
sqlite_path = 'instance/budget.db'
if not os.path.exists(sqlite_path):
    sqlite_path = 'budget.db'
if not os.path.exists(sqlite_path):
    print("❌ budget.db not found")
    exit(1)

src = sqlite3.connect(sqlite_path)
src.row_factory = sqlite3.Row
cur = src.cursor()

tables = [
    'wallet', 'transaction', 'fixed_expense', 'credit_card',
    'loan', 'budget_planner', 'goal', 'recurring_payment',
    'wallet_transfer', 'net_worth_history', 'notification', 'app_settings'
]

data = {}
for table in tables:
    try:
        cur.execute(f'SELECT * FROM "{table}"')
        rows = cur.fetchall()
        data[table] = [dict(r) for r in rows]
        print(f"  📋 {table}: {len(rows)} rows")
    except Exception as e:
        print(f"  ⚠️  {table}: {e}")
        data[table] = []

src.close()

# Step 3: Insert into PostgreSQL
print("\nStep 3 — Inserting into PostgreSQL...")
from models import (db, Wallet, Transaction, FixedExpense, CreditCard, Loan,
                    BudgetPlanner, Goal, RecurringPayment, WalletTransfer,
                    NetWorthHistory, Notification, AppSettings)
from datetime import datetime, date

def parse_date(val):
    if not val:
        return None
    if isinstance(val, (date, datetime)):
        return val
    for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d']:
        try:
            return datetime.strptime(str(val), fmt)
        except:
            pass
    return None

def parse_date_only(val):
    if not val:
        return None
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val)[:10], '%Y-%m-%d').date()
    except:
        return None

with app.app_context():
    total = 0

    for row in data.get('wallet', []):
        try:
            db.session.add(Wallet(
                id=row['id'], name=row['name'],
                wallet_type=row.get('wallet_type'),
                balance=row.get('balance', 0),
                currency=row.get('currency', 'LKR'),
                color=row.get('color', '#6366f1'),
                notes=row.get('notes'),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  wallet row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ wallet: {len(data.get('wallet', []))} rows")

    for row in data.get('transaction', []):
        try:
            db.session.add(Transaction(
                id=row['id'],
                trans_type=row.get('trans_type'),
                amount=row.get('amount', 0),
                description=row.get('description'),
                category=row.get('category', 'General'),
                date=parse_date(row.get('date')),
                notes=row.get('notes'),
                tags=row.get('tags'),
                loan_id=row.get('loan_id'),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  transaction row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ transaction: {len(data.get('transaction', []))} rows")

    for row in data.get('fixed_expense', []):
        try:
            db.session.add(FixedExpense(
                id=row['id'], name=row['name'],
                amount=row.get('amount', 0),
                date=parse_date_only(row.get('date')),
                repeat=bool(row.get('repeat', 0)),
                repeat_until=parse_date_only(row.get('repeat_until')),
                is_income=bool(row.get('is_income', 0)),
                category=row.get('category', 'General'),
                notes=row.get('notes'),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  fixed_expense row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ fixed_expense: {len(data.get('fixed_expense', []))} rows")

    for row in data.get('credit_card', []):
        try:
            db.session.add(CreditCard(
                id=row['id'],
                bank_name=row.get('bank_name'),
                card_number=row.get('card_number'),
                credit_limit=row.get('credit_limit', 0),
                available_balance=row.get('available_balance', 0),
                minimum_payment=row.get('minimum_payment', 0),
                due_date=parse_date_only(row.get('due_date')),
                interest_rate=row.get('interest_rate', 0),
                notes=row.get('notes'),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  credit_card row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ credit_card: {len(data.get('credit_card', []))} rows")

    for row in data.get('loan', []):
        try:
            db.session.add(Loan(
                id=row['id'],
                loan_name=row.get('loan_name', ''),
                lender_name=row.get('lender_name', ''),
                principal_amount=row.get('principal_amount', 0),
                interest_rate=row.get('interest_rate', 0),
                loan_term=row.get('loan_term', 0),
                start_date=parse_date_only(row.get('start_date')),
                monthly_payment=row.get('monthly_payment', 0),
                payment_frequency=row.get('payment_frequency', 'Monthly'),
                next_due_date=parse_date_only(row.get('next_due_date')),
                outstanding_balance=row.get('outstanding_balance', 0),
                loan_status=row.get('loan_status', 'Active'),
                notes=row.get('notes'),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  loan row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ loan: {len(data.get('loan', []))} rows")

    for row in data.get('budget_planner', []):
        try:
            db.session.add(BudgetPlanner(
                id=row['id'],
                category=row.get('category', ''),
                amount=row.get('amount', 0),
                month=row.get('month', ''),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  budget_planner row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ budget_planner: {len(data.get('budget_planner', []))} rows")

    for row in data.get('goal', []):
        try:
            db.session.add(Goal(
                id=row['id'], name=row.get('name', ''),
                description=row.get('description'),
                target_amount=row.get('target_amount', 0),
                current_amount=row.get('current_amount', 0),
                target_date=parse_date_only(row.get('target_date')),
                category=row.get('category', 'Savings'),
                status=row.get('status', 'active'),
                wallet_id=row.get('wallet_id'),
                icon=row.get('icon', '🎯'),
                color=row.get('color', '#6366f1'),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  goal row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ goal: {len(data.get('goal', []))} rows")

    for row in data.get('recurring_payment', []):
        try:
            db.session.add(RecurringPayment(
                id=row['id'], name=row.get('name', ''),
                amount=row.get('amount', 0),
                frequency=row.get('frequency', 'monthly'),
                next_date=parse_date_only(row.get('next_date')),
                end_date=parse_date_only(row.get('end_date')),
                category=row.get('category', 'General'),
                wallet_id=row.get('wallet_id'),
                credit_card_id=row.get('credit_card_id'),
                is_active=bool(row.get('is_active', 1)),
                auto_apply=bool(row.get('auto_apply', 0)),
                last_applied=parse_date_only(row.get('last_applied')),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  recurring_payment row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ recurring_payment: {len(data.get('recurring_payment', []))} rows")

    for row in data.get('wallet_transfer', []):
        try:
            db.session.add(WalletTransfer(
                id=row['id'],
                from_wallet_id=row.get('from_wallet_id'),
                to_wallet_id=row.get('to_wallet_id'),
                amount=row.get('amount', 0),
                note=row.get('note'),
                date=parse_date(row.get('date')),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  wallet_transfer row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ wallet_transfer: {len(data.get('wallet_transfer', []))} rows")

    for row in data.get('net_worth_history', []):
        try:
            db.session.add(NetWorthHistory(
                id=row['id'],
                date=parse_date_only(row.get('date')),
                total_assets=row.get('total_assets', 0),
                total_liabilities=row.get('total_liabilities', 0),
                net_worth=row.get('net_worth', 0),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  net_worth_history row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ net_worth_history: {len(data.get('net_worth_history', []))} rows")

    for row in data.get('notification', []):
        try:
            db.session.add(Notification(
                id=row['id'],
                title=row.get('title', ''),
                message=row.get('message', ''),
                notif_type=row.get('notif_type', 'info'),
                is_read=bool(row.get('is_read', 0)),
                created_at=parse_date(row.get('created_at')),
                related_type=row.get('related_type'),
                related_id=row.get('related_id'),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  notification row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ notification: {len(data.get('notification', []))} rows")

    for row in data.get('app_settings', []):
        try:
            db.session.add(AppSettings(
                id=row['id'],
                key=row.get('key', ''),
                value=row.get('value'),
                user_id=row.get('user_id')
            ))
            total += 1
        except Exception as e:
            print(f"  ⚠️  app_settings row {row.get('id')}: {e}")

    db.session.commit()
    print(f"  ✅ app_settings: {len(data.get('app_settings', []))} rows")

    print(f"\n✅ Migration complete! {total} total rows moved to PostgreSQL.")
    print("🚀 Run: python app.py")
