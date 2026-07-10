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

    # Which wallet/card this transaction actually moved money in/out of —
    # needed so deleting or editing a transaction can correctly reverse
    # the exact balance change it made. balance_applied tracks whether
    # the wallet/card balance was actually touched (future-dated
    # transactions don't touch balances until their date arrives).
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'), nullable=True)
    credit_card_id = db.Column(db.Integer, db.ForeignKey('credit_card.id'), nullable=True)
    balance_applied = db.Column(db.Boolean, default=False)
    wallet = db.relationship('Wallet', backref=db.backref('transactions', lazy=True))
    credit_card = db.relationship('CreditCard', backref=db.backref('transactions', lazy=True))

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
    key = db.Column(db.String(100), nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Investment(db.Model):
    """Tracks investments: stocks, unit trusts, FDs, crypto, gold."""
    __tablename__ = 'investment'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    name = db.Column(db.String(200), nullable=False)          # e.g. "John Keells Holdings"
    symbol = db.Column(db.String(20), nullable=True)           # e.g. "JKH.N0000"
    asset_type = db.Column(db.String(50), nullable=False)      # Stock, Unit Trust, FD, Crypto, Gold, Other
    institution = db.Column(db.String(100), nullable=True)     # bank/broker name
    units = db.Column(db.Float, default=0)                     # shares/units held
    purchase_price = db.Column(db.Float, default=0)            # cost per unit
    current_price = db.Column(db.Float, default=0)             # current market price per unit
    purchase_date = db.Column(db.Date, nullable=True)
    maturity_date = db.Column(db.Date, nullable=True)          # for FDs
    interest_rate = db.Column(db.Float, default=0)             # for FDs
    currency = db.Column(db.String(10), default='LKR')
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='active')        # active, sold, matured
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'), nullable=True)
    wallet = db.relationship('Wallet', backref='investments', foreign_keys=[wallet_id])
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def total_invested(self):
        return self.units * self.purchase_price

    @property
    def current_value(self):
        return self.units * self.current_price

    @property
    def gain_loss(self):
        return self.current_value - self.total_invested

    @property
    def gain_loss_pct(self):
        if self.total_invested > 0:
            return (self.gain_loss / self.total_invested) * 100
        return 0


class InvestmentIncome(db.Model):
    """Dividends, interest, maturity payouts from investments."""
    __tablename__ = 'investment_income'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    investment_id = db.Column(db.Integer, db.ForeignKey('investment.id'), nullable=True)
    investment = db.relationship('Investment', backref='income_records')
    income_type = db.Column(db.String(50), nullable=False)     # Dividend, Interest, Maturity, Capital Gain
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False)
    action = db.Column(db.String(20), default='record_only')   # add_to_wallet, reinvest, record_only
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'), nullable=True)
    wallet = db.relationship('Wallet', backref='investment_income', foreign_keys=[wallet_id])
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Debt(db.Model):
    """Friend/personal debt tracking."""
    __tablename__ = 'debt'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=True, index=True)
    contact_name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    direction = db.Column(db.String(10), nullable=False)       # 'owe' = I owe them, 'lent' = they owe me
    description = db.Column(db.String(200), nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default='pending')       # pending, settled, partial
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'), nullable=True)
    wallet = db.relationship('Wallet', backref='debts', foreign_keys=[wallet_id])
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class FavouriteStock(db.Model):
    """User's favourite CSE stocks."""
    __tablename__ = 'favourite_stock'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=False, index=True)
    symbol = db.Column(db.String(30), nullable=False)   # e.g. JKH.N0000
    display_name = db.Column(db.String(100), nullable=True)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)

class Bank(db.Model):
    """Simple directory of Sri Lankan banks for the CC Offers page —
    just a logo and a link to the bank's own official offers page.
    Replaces the old scraped/crowd-submitted offer system, which
    testers repeatedly flagged as showing mismatched or stale deals.
    Admin-managed only."""
    __tablename__ = 'bank'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    short_code = db.Column(db.String(6), nullable=True)   # e.g. "CB", "HNB" — used as logo fallback
    logo_url = db.Column(db.String(500), nullable=True)   # uploaded/hosted logo image
    offers_url = db.Column(db.String(500), nullable=False)  # the bank's own CC offers page
    color = db.Column(db.String(20), default="accent")    # CDS role used for the logo-fallback tile
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DailyTemplate(db.Model):
    """A named daily expense template — e.g. 'Weekday' with items like
    Tuktuk 400, Breakfast 150, Lunch 300. One tap applies the whole list
    as real transactions without the user typing anything."""
    __tablename__ = 'daily_template'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)          # e.g. "Weekday", "Office Day"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('DailyTemplateItem', backref='template',
                            cascade='all, delete-orphan', lazy=True,
                            order_by='DailyTemplateItem.sort_order')


class DailyTemplateItem(db.Model):
    """A single line item within a DailyTemplate."""
    __tablename__ = 'daily_template_item'
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('daily_template.id'), nullable=False)
    description = db.Column(db.String(100), nullable=False)   # e.g. "Tuktuk"
    amount = db.Column(db.Float, nullable=False)              # e.g. 400.0
    category = db.Column(db.String(100), default='General')
    sort_order = db.Column(db.Integer, default=0)