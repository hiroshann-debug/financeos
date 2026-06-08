from datetime import datetime, timezone, date
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    trans_type = db.Column(db.String(10))
    amount = db.Column(db.Float)
    description = db.Column(db.String(100))
    category = db.Column(db.String(100), default="General")
    date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    notes = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(200), nullable=True)  # comma-separated tags
    loan_id = db.Column(
        db.Integer,
        db.ForeignKey('loan.id', name='fk_transaction_loan_id'),
        nullable=True
    )
    loan = db.relationship('Loan', backref=db.backref('transactions', lazy=True))

class FixedExpense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=date.today)
    repeat = db.Column(db.Boolean, default=False)
    repeat_until = db.Column(db.Date, nullable=True)
    is_income = db.Column(db.Boolean, default=False)
    category = db.Column(db.String(100), nullable=False)
    notes = db.Column(db.Text, nullable=True)

class CreditCard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    bank_name = db.Column(db.String(100))
    card_number = db.Column(db.String(20))
    credit_limit = db.Column(db.Float)
    available_balance = db.Column(db.Float)
    minimum_payment = db.Column(db.Float)
    due_date = db.Column(db.Date)
    interest_rate = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text, nullable=True)

class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    category = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    month = db.Column(db.String(7), nullable=False)

class Loan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    loan_name = db.Column(db.String(100), nullable=False)
    lender_name = db.Column(db.String(100), nullable=False)
    principal_amount = db.Column(db.Float, nullable=False)
    interest_rate = db.Column(db.Float, nullable=False)
    loan_term = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    monthly_payment = db.Column(db.Float, nullable=False)
    payment_frequency = db.Column(db.String(20), nullable=False)
    next_due_date = db.Column(db.Date, nullable=False)
    outstanding_balance = db.Column(db.Float, nullable=False)
    loan_status = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Wallet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    name = db.Column(db.String(100), nullable=False)
    wallet_type = db.Column(db.String(50))
    balance = db.Column(db.Float, default=0.0)
    currency = db.Column(db.String(10), default="LKR")
    color = db.Column(db.String(20), default="#6366f1")
    notes = db.Column(db.Text, nullable=True)

class WalletTransfer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    from_wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'), nullable=False)
    to_wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(200), nullable=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    from_wallet = db.relationship('Wallet', foreign_keys=[from_wallet_id], backref='transfers_out')
    to_wallet = db.relationship('Wallet', foreign_keys=[to_wallet_id], backref='transfers_in')

class NetWorthHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    date = db.Column(db.Date, default=date.today, nullable=False)
    total_assets = db.Column(db.Float, nullable=False)
    total_liabilities = db.Column(db.Float, nullable=False)
    net_worth = db.Column(db.Float, nullable=False)

class BudgetPlanner(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    category = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    month = db.Column(db.String(7), nullable=False)

# --- NEW MODELS ---

class Goal(db.Model):
    """Financial savings goals with progress tracking."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    target_amount = db.Column(db.Float, nullable=False)
    current_amount = db.Column(db.Float, default=0.0)
    target_date = db.Column(db.Date, nullable=True)
    category = db.Column(db.String(50), default="Savings")  # Emergency, Vacation, Car, House, etc.
    status = db.Column(db.String(20), default="active")  # active, completed, paused
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'), nullable=True)
    wallet = db.relationship('Wallet', backref='goals')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    icon = db.Column(db.String(10), default="🎯")
    color = db.Column(db.String(20), default="#6366f1")

class Notification(db.Model):
    """Smart alerts and reminders."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    notif_type = db.Column(db.String(30), default="info")  # info, warning, danger, success
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    related_type = db.Column(db.String(50), nullable=True)  # loan, credit_card, budget, goal
    related_id = db.Column(db.Integer, nullable=True)

class RecurringPayment(db.Model):
    """Auto-scheduled recurring payments beyond fixed expenses."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    frequency = db.Column(db.String(20), nullable=False)  # weekly, biweekly, monthly, quarterly, yearly
    next_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    category = db.Column(db.String(100), default="General")
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'), nullable=True)
    credit_card_id = db.Column(db.Integer, db.ForeignKey('credit_card.id'), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    auto_apply = db.Column(db.Boolean, default=False)
    last_applied = db.Column(db.Date, nullable=True)
    wallet = db.relationship('Wallet', backref='recurring_payments')
    credit_card = db.relationship('CreditCard', backref='recurring_payments')

class AppSettings(db.Model):
    """User preferences and app configuration."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
