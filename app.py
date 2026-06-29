from flask import Flask, render_template, request, redirect, flash, url_for, g, jsonify, Response, session
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import atexit
from models import db, Transaction, FixedExpense, CreditCard, Loan, Wallet, WalletTransfer, \
    NetWorthHistory, BudgetPlanner, Goal, Notification, RecurringPayment, AppSettings, \
    Investment, InvestmentIncome, Debt, FavouriteStock, Bank, Category
import requests as req_lib
from calendar import monthrange
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
import json, csv, io, os, re
from collections import defaultdict
from flask_migrate import Migrate
from pdf_report import generate_monthly_report
from finance_service import (add_transaction, update_networth_snapshot, check_and_create_notifications,
                              apply_due_recurring_payments, get_spending_insights, get_financial_health_score,
                              get_setting, get_settings, set_setting, reverse_transaction_balance)
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from functools import wraps
import requests as http

from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(
    __name__,
    template_folder=os.path.abspath(os.path.join(os.path.dirname(__file__), 'templates')),
    static_folder=os.path.abspath(os.path.join(os.path.dirname(__file__), 'static'))
)

# Railway / Reverse Proxy support
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Database
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///budget.db')

if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}

# Security
app.secret_key = os.getenv('APP_SECRET_KEY')

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PREFERRED_URL_SCHEME='https'
)

db.init_app(app)
migrate = Migrate(app, db)

# ─────────────────────────────────────────
#  Auth0 Setup
# ─────────────────────────────────────────
oauth = OAuth(app)
auth0 = oauth.register(
    'auth0',
    client_id=os.getenv('AUTH0_CLIENT_ID'),
    client_secret=os.getenv('AUTH0_CLIENT_SECRET'),
    client_kwargs={'scope': 'openid profile email'},
    server_metadata_url=f'https://{os.getenv("AUTH0_DOMAIN")}/.well-known/openid-configuration'
)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────
def uid():
    user = session.get('user')
    return user['id'] if user else None

def currency_fmt(amount):
    sym = get_setting("currency_symbol", "LKR", user_id=uid())
    return f"{sym} {amount:,.2f}" 


# ─────────────────────────────────────────
#  Error handlers
# ─────────────────────────────────────────
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

@app.errorhandler(403)
def forbidden(e):
    return render_template('404.html'), 403


# ─────────────────────────────────────────
#  Validation helpers
# ─────────────────────────────────────────
def validate_amount(value, field_name="Amount"):
    try:
        amount = float(value)
        if amount <= 0:
            return None, f"{field_name} must be greater than zero."
        if amount > 999_000_000:
            return None, f"{field_name} is too large."
        return round(amount, 2), None
    except (TypeError, ValueError):
        return None, f"{field_name} must be a valid number."

def validate_date(value, field_name="Date"):
    if not value:
        return None, f"{field_name} is required."
    try:
        return datetime.strptime(value, '%Y-%m-%d').date(), None
    except ValueError:
        return None, f"{field_name} must be a valid date (YYYY-MM-DD)."

def validate_text(value, field_name="Field", max_len=100, required=True):
    if not value or not str(value).strip():
        if required:
            return None, f"{field_name} is required."
        return '', None
    value = str(value).strip()
    if len(value) > max_len:
        return None, f"{field_name} must be under {max_len} characters."
    return value, None


def get_salary_period(for_date):
    cycle_day = int(get_setting("salary_cycle_day", "25", user_id=uid()))
    if for_date.day >= cycle_day:
        period_start = for_date.replace(day=cycle_day)
        period_end = (for_date + relativedelta(months=1)).replace(day=cycle_day) - timedelta(days=1)
    else:
        period_start = (for_date - relativedelta(months=1)).replace(day=cycle_day)
        period_end = for_date.replace(day=cycle_day) - timedelta(days=1)
    return period_start, period_end


# ─────────────────────────────────────────
#  Context processors
# ─────────────────────────────────────────
@app.context_processor
def inject_globals():
    user = session.get('user')
    user_id = user['id'] if user else None
    unread_count = Notification.query.filter_by(is_read=False, user_id=user_id).count() if user_id else 0
    settings = get_settings(
        ["dark_mode", "currency_symbol"],
        defaults={"dark_mode": "false", "currency_symbol": "LKR"},
        user_id=user_id,
    )
    return dict(
        unread_notifications=unread_count,
        dark_mode=settings["dark_mode"] == "true",
        currency_symbol=settings["currency_symbol"],
        current_user=user
    )


# ─────────────────────────────────────────
#  Core routes
# ─────────────────────────────────────────
@app.route('/')
def index():
    return redirect(url_for('dashboard'))


@app.route('/landing')
def landing():
    return render_template('landing.html')


@app.route('/login')
def login():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')


@app.route('/signup')
def signup():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return render_template('signup.html')


@app.route('/auth/login')
def auth_login():
    return auth0.authorize_redirect(
        redirect_uri=url_for('callback', _external=True)
    )


@app.route('/auth/signup')
def auth_signup():
    return auth0.authorize_redirect(
        redirect_uri=url_for('callback', _external=True),
        screen_hint='signup'
    )



# ─────────────────────────────────────────
#  Email — Resend.com
# ─────────────────────────────────────────
import os
import requests as http
import logging

logger = logging.getLogger(__name__)

def send_email(to_email, subject, html_body):
    api_key = os.environ.get("RESEND_API_KEY")

    if not api_key:
        logger.warning("RESEND_API_KEY not set — email not sent")
        return False

    if not to_email or "@" not in to_email:
        logger.warning("Invalid email address")
        return False

    try:
        response = http.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": "FinanceOS <noreply@financeos.app>",
                "to": [to_email],
                "subject": subject,
                "html": html_body,
            },
            timeout=10,
        )

        if response.status_code not in (200, 201):
            logger.error(f"Email failed: {response.text}")

        return response.status_code in (200, 201)

    except Exception as e:
        logger.error(f"Email error: {e}")
        return False



def send_welcome_email(to_email, name):
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
body{{margin:0;padding:0;background:#f0ebe3;font-family:'Segoe UI',Arial,sans-serif;}}
.wrap{{max-width:580px;margin:0 auto;padding:32px 16px;}}
.card{{background:#fff;border-radius:20px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);}}
.hero{{background:linear-gradient(135deg,#0f172a,#1e293b);padding:40px 32px;text-align:center;}}
.logo-icon{{display:inline-block;width:44px;height:44px;background:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:12px;color:white;font-size:1.3rem;font-weight:800;line-height:44px;text-align:center;}}
.hero h1{{color:white;font-size:1.6rem;font-weight:800;margin:16px 0 8px;}}
.hero p{{color:rgba(255,255,255,0.5);font-size:0.85rem;margin:0;}}
.body{{padding:32px;}}
.greeting{{font-size:1.1rem;font-weight:700;color:#1a1f37;margin-bottom:12px;}}
.text{{font-size:0.875rem;color:#64748b;line-height:1.75;margin-bottom:20px;}}
.feature{{display:flex;gap:12px;padding:14px;background:#f8fafc;border-radius:12px;border:1px solid #e2e8f0;margin-bottom:10px;}}
.fi{{width:36px;height:36px;border-radius:10px;display:inline-flex;align-items:center;justify-content:center;font-size:1rem;flex-shrink:0;}}
.ft{{font-size:0.85rem;font-weight:700;color:#1a1f37;margin-bottom:2px;}}
.fd{{font-size:0.78rem;color:#64748b;}}
.cta{{text-align:center;margin:28px 0;}}
.btn{{display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:white;text-decoration:none;border-radius:12px;font-size:0.9rem;font-weight:700;}}
.footer{{padding:20px 32px;text-align:center;border-top:1px solid #f1f5f9;}}
.footer p{{font-size:0.75rem;color:#94a3b8;margin:4px 0;}}
.footer a{{color:#6366f1;text-decoration:none;}}
</style>
</head>
<body>
<div class="wrap"><div class="card">
<div class="hero">
  <div class="logo-icon">F</div>
  <h1>Welcome to FinanceOS! 🎉</h1>
  <p>Sri Lanka's Personal Financial Operating System</p>
  <p style="font-size:0.75rem;color:rgba(255,255,255,0.3);margin-top:4px;">FinanceOS වෙත සාදරයෙන් පිළිගනිමු!</p>
</div>
<div class="body">
  <div class="greeting">Hey {name}! 👋</div>
  <p class="text">Your FinanceOS account is ready. Take control of your finances with everything built specifically for Sri Lanka.</p>
  <div class="feature"><div class="fi" style="background:#eff6ff;">📊</div><div><div class="ft">Smart Dashboard</div><div class="fd">Salary cycle, net worth, savings rate and freedom score at a glance.</div></div></div>
  <div class="feature"><div class="fi" style="background:#f5f3ff;">🧠</div><div><div class="ft">AI Financial Advisor</div><div class="fd">Ask anything about your finances. Powered by Claude AI with your real data.</div></div></div>
  <div class="feature"><div class="fi" style="background:#fce7f3;">🎁</div><div><div class="ft">Sri Lanka CC Offers</div><div class="fd">Live deals from ComBank, Sampath, HNB, BOC and more.</div></div></div>
  <div class="feature"><div class="fi" style="background:#f0fdf4;">📈</div><div><div class="ft">CSE Live Stocks</div><div class="fd">Real-time Colombo Stock Exchange prices and your favourites.</div></div></div>
  <div class="cta">
    <a href="https://brave-grace-production-6691.up.railway.app/dashboard" class="btn">🚀 Go to Your Dashboard</a>
    <p style="font-size:0.72rem;color:#94a3b8;margin-top:10px;">Get started: Add wallets → Log income → Ask AI Advisor</p>
  </div>
</div>
<div class="footer">
  <p>Built with ❤️ in Sri Lanka 🇱🇰 · © 2026 FinanceOS</p>
  <p><a href="https://brave-grace-production-6691.up.railway.app/privacy">Privacy</a> · <a href="https://brave-grace-production-6691.up.railway.app/terms">Terms</a></p>
</div>
</div></div>
</body>
</html>"""
    send_email(to_email, f"Welcome to FinanceOS, {name}! 🎉", html)


@app.route('/callback')
def callback():
    token = auth0.authorize_access_token()
    userinfo = token.get('userinfo')
    # ── Use email as universal user ID ──
    user_email = userinfo.get('email', '').lower().strip()
    if not user_email:
        flash("Could not retrieve email from login provider.", "danger")
        return redirect(url_for('landing'))

    user_id = user_email
    user_name = userinfo.get('name', 'User').split()[0]
    provider = userinfo.get('sub', '').split('|')[0]

    session['user'] = {
        'id': user_id,
        'name': userinfo.get('name', 'User'),
        'email': user_email,
        'picture': userinfo.get('picture', ''),
        'provider': provider,
    }

    set_setting('email', user_email, user_id=user_id)

    with app.app_context():
        is_new = not AppSettings.query.filter_by(
            user_id=user_id, key='welcome_sent'
        ).first()
        if is_new:
            send_welcome_email(user_email, user_name)
            set_setting('welcome_sent', 'true', user_id=user_id)

    flash(f"Welcome back, {user_name}! 👋", "success")
    return redirect(url_for('dashboard'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(
        f"https://{os.getenv('AUTH0_DOMAIN')}/v2/logout?"
        f"returnTo={url_for('landing', _external=True)}"
        f"&client_id={os.getenv('AUTH0_CLIENT_ID')}"
    )


# ─────────────────────────────────────────
#  Dashboard
# ─────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    apply_due_recurring_payments(user_id=uid())
    check_and_create_notifications(user_id=uid())

    wallets = Wallet.query.filter_by(user_id=uid()).all()
    today = date.today()
    period_start, period_end = get_salary_period(today)

    month_str = request.args.get("month")
    if month_str:
        try:
            year, month_num = map(int, month_str.split("-"))
            cycle_day = int(get_setting("salary_cycle_day", "25"))
            override_date = date(year, month_num, cycle_day)
            period_start, period_end = get_salary_period(override_date)
        except:
            pass

    # Single source of truth for this period's transactions — used both
    # for display (includes future-dated rows, correctly flagged via
    # future_txn_ids) and, via transactions_occurred below, for actual
    # financial calculations (which must exclude anything not yet due).
    #
    # Previously this was TWO separate, differently-filtered queries
    # (a calendar-month one feeding future_txn_ids, and a salary-period
    # one feeding the actual displayed table) — their transaction IDs
    # didn't reliably correspond to each other, so the "upcoming" badge
    # in the Recent Transactions table could end up misapplied.
    transactions = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.date >= period_start,
        Transaction.date <= period_end
    ).order_by(Transaction.date.desc()).all()

    future_txn_ids = set(
        t.id for t in transactions
        if (t.date.date() if isinstance(t.date, datetime) else t.date) > today
    )

    # Period ACTUALS should only reflect what's actually happened —
    # a future-dated transaction within this period hasn't occurred
    # yet, so it shouldn't count toward total_income/total_expense.
    transactions_occurred = [
        t for t in transactions
        if (t.date.date() if isinstance(t.date, datetime) else t.date) <= today
    ]

    income_by_category = defaultdict(float)
    expense_by_category = defaultdict(float)
    total_income = 0
    total_expense = 0

    for t in transactions_occurred:
        category = t.category or t.description or "Other"
        if t.trans_type == "income":
            total_income += t.amount
            income_by_category[category] += t.amount
        elif t.trans_type == "expense":
            total_expense += t.amount
            expense_by_category[category] += t.amount

    # Total Wallet Balance = actual wallet balances (source of truth, independent of transaction calc)
    total_wallet_balance = sum(w.balance for w in wallets)
    # Net Balance = wallet balance is the real number; transaction-based is for period tracking
    # We show both: wallet balance = actual cash, net balance = period performance

    chart_data = {
        "income": dict(income_by_category),
        "expense": dict(expense_by_category),
    }

    # Names already paid this period
    # Only transactions that have ACTUALLY happened count as "paid" —
    # a future-dated transaction within this period shouldn't mark a
    # fixed expense/recurring payment as already paid before its date.
    paid_names_period = set(t.description for t in transactions_occurred if t.trans_type == 'expense')

    upcoming_payments = []

    # 1. Credit card due dates
    cards_all = CreditCard.query.filter_by(user_id=uid()).all()
    upcoming_30 = today + timedelta(days=30)
    for c in cards_all:
        if c.due_date and today <= c.due_date <= upcoming_30:
            days_left = (c.due_date - today).days
            # Check if min payment already recorded as expense this period
            card_paid = Transaction.query.filter(
                Transaction.user_id == uid(),
                Transaction.description == f"{c.bank_name} Card Payment",
                Transaction.trans_type == "expense",
                Transaction.date >= period_start,
                Transaction.date <= today
            ).first()
            upcoming_payments.append({
                "kind": "card",
                "type": f"{c.bank_name} Card",
                "amount": c.minimum_payment,
                "due_date": c.due_date.strftime('%Y-%m-%d'),
                "days_left": days_left,
                "paid": bool(card_paid),
                "id": c.id,
            })

    # 1b. Loan installments due in next 30 days
    loans_all = Loan.query.filter_by(user_id=uid(), loan_status='Active').all()
    for l in loans_all:
        if l.next_due_date and today <= l.next_due_date <= upcoming_30:
            days_left = (l.next_due_date - today).days
            loan_paid = Transaction.query.filter(
                Transaction.user_id == uid(),
                Transaction.description == f"Loan payment: {l.loan_name}",
                Transaction.trans_type == "expense",
                Transaction.date >= period_start,
                Transaction.date <= today
            ).first()
            upcoming_payments.append({
                "kind": "loan",
                "type": f"{l.loan_name}",
                "amount": l.monthly_payment,
                "due_date": l.next_due_date.strftime('%Y-%m-%d'),
                "days_left": days_left,
                "paid": bool(loan_paid),
                "id": l.id,
            })

    # 2. Fixed expenses due this period (unpaid only)
    fixed_expenses_all = FixedExpense.query.filter_by(user_id=uid()).all()
    for exp in fixed_expenses_all:
        repeat_until = exp.repeat_until or date.max
        if exp.repeat and repeat_until >= period_start:
            next_due = exp.date
            while next_due < period_start:
                next_due += relativedelta(months=1)
            in_period = period_start <= next_due <= period_end and next_due <= repeat_until
        else:
            next_due = exp.date
            in_period = not exp.repeat and period_start <= next_due <= period_end

        if in_period:
            paid = exp.name in paid_names_period
            days_left = (next_due - today).days
            upcoming_payments.append({
                "kind": "fixed",
                "type": exp.name,
                "amount": exp.amount,
                "due_date": next_due.strftime('%Y-%m-%d'),
                "days_left": days_left,
                "paid": paid,
                "id": exp.id,
            })

    # 3. Recurring payments due this period (unpaid only)
    recurring_all = RecurringPayment.query.filter_by(is_active=True, user_id=uid()).all()
    for rec in recurring_all:
        if period_start <= rec.next_date <= period_end:
            paid = rec.name in paid_names_period
            days_left = (rec.next_date - today).days
            upcoming_payments.append({
                "kind": "recurring",
                "type": rec.name,
                "amount": rec.amount,
                "due_date": rec.next_date.strftime('%Y-%m-%d'),
                "days_left": days_left,
                "paid": paid,
                "id": rec.id,
            })

    # Show only UNPAID, sorted by due date
    unpaid = [p for p in upcoming_payments if not p["paid"]]
    upcoming_fixed_sorted = sorted(unpaid, key=lambda x: x["due_date"])

    # Salary bar calculations
    unpaid_fixed_total = sum(p["amount"] for p in unpaid)
    days_left_in_period = max(0, (period_end - today).days)
    days_total_period = max(1, (period_end - period_start).days + 1)
    days_elapsed = max(1, (today - period_start).days + 1)
    # Daily average spend so far
    daily_avg_so_far = total_expense / days_elapsed if days_elapsed > 0 else 0
    # How much left after unpaid fixed
    available_after_fixed = total_wallet_balance - unpaid_fixed_total
    # Daily budget remaining
    daily_budget_remaining = available_after_fixed / days_left_in_period if days_left_in_period > 0 else 0

    cards = CreditCard.query.filter_by(user_id=uid()).all()
    total_credit_limit = sum(c.credit_limit for c in cards)
    total_available = sum(c.available_balance for c in cards)
    total_used = total_credit_limit - total_available
    total_monthly_due = sum(c.minimum_payment for c in cards)

    fixed_chart_data = defaultdict(float)
    for f in fixed_expenses_all:
        repeat_until = f.repeat_until or date.max
        if f.repeat and repeat_until >= period_start:
            next_due = f.date
            while next_due < period_start:
                next_due += relativedelta(months=1)
            if period_start <= next_due <= period_end:
                fixed_chart_data[f.category] += f.amount
        elif not f.repeat and period_start <= f.date <= period_end:
            fixed_chart_data[f.category] += f.amount

    six_month_anchor = period_start - relativedelta(months=5)
    expenses_last_six = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.trans_type == 'expense',
        Transaction.date >= six_month_anchor
    ).all()
    monthly_expenses = defaultdict(float)
    monthly_income_map = defaultdict(float)
    for t in expenses_last_six:
        month_key = t.date.strftime("%Y-%m") if isinstance(t.date, date) else t.date.date().strftime("%Y-%m")
        monthly_expenses[month_key] += t.amount
    income_last_six = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.trans_type == 'income',
        Transaction.date >= six_month_anchor
    ).all()
    for t in income_last_six:
        month_key = t.date.strftime("%Y-%m") if isinstance(t.date, date) else t.date.date().strftime("%Y-%m")
        monthly_income_map[month_key] += t.amount

    monthly_labels = sorted(set(list(monthly_expenses.keys()) + list(monthly_income_map.keys())))
    monthly_values = [monthly_expenses[m] for m in monthly_labels]
    monthly_income_values = [monthly_income_map[m] for m in monthly_labels]

    next_period_start = period_start + relativedelta(months=1)
    next_period_end = next_period_start + relativedelta(months=1) - timedelta(days=1)
    next_month_expenses = []
    for exp in fixed_expenses_all:
        repeat_until = exp.repeat_until or date.max
        if (exp.repeat or exp.repeat_until) and repeat_until >= next_period_start:
            next_due = exp.date
            while next_due < next_period_start:
                next_due += relativedelta(months=1)
            if next_period_start <= next_due <= next_period_end and next_due <= repeat_until:
                next_month_expenses.append({
                    "id": exp.id, "name": exp.name, "amount": exp.amount,
                    "date": next_due, "repeat": exp.repeat, "category": exp.category or "Uncategorized"
                })
        elif not exp.repeat and next_period_start <= exp.date <= next_period_end:
            next_month_expenses.append({
                "id": exp.id, "name": exp.name, "amount": exp.amount,
                "date": exp.date, "repeat": False, "category": exp.category or "Uncategorized"
            })

    # Loan installments — only if the loan's actual next due date falls
    # within next period (previously this added EVERY active loan
    # unconditionally, which overstated the forecast).
    for l in Loan.query.filter_by(user_id=uid(), loan_status='Active').all():
        if l.next_due_date:
            next_due = l.next_due_date
            # Step forward to the occurrence that actually falls next period
            while next_due < next_period_start:
                next_due += relativedelta(months=1)
            if next_period_start <= next_due <= next_period_end:
                next_month_expenses.append({
                    "id": l.id, "name": f"Loan: {l.loan_name}",
                    "amount": l.monthly_payment,
                    "date": next_due,
                    "repeat": True, "category": "Loan Payment"
                })

    # Credit card minimum payments — only if the due date actually
    # falls within next period (previously this added EVERY card that
    # merely had a due_date set, regardless of when it actually fell).
    for c in CreditCard.query.filter_by(user_id=uid()).all():
        if c.due_date:
            next_due = c.due_date
            while next_due < next_period_start:
                next_due += relativedelta(months=1)
            if next_period_start <= next_due <= next_period_end:
                next_month_expenses.append({
                    "id": c.id, "name": f"{c.bank_name} Card Min.",
                    "amount": c.minimum_payment,
                    "date": next_due,
                    "repeat": True, "category": "Credit Card"
                })

    # Recurring payments — was completely missing from this forecast
    # even though it's included in the current-period upcoming_payments
    # list above, which made the two forecasts inconsistent.
    for rec in RecurringPayment.query.filter_by(is_active=True, user_id=uid()).all():
        next_due = rec.next_date
        while next_due < next_period_start:
            if rec.frequency == "weekly":
                next_due += timedelta(weeks=1)
            elif rec.frequency == "biweekly":
                next_due += timedelta(weeks=2)
            elif rec.frequency == "quarterly":
                next_due += relativedelta(months=3)
            elif rec.frequency == "yearly":
                next_due += relativedelta(years=1)
            else:
                next_due += relativedelta(months=1)
        if next_period_start <= next_due <= next_period_end and (not rec.end_date or next_due <= rec.end_date):
            next_month_expenses.append({
                "id": rec.id, "name": rec.name,
                "amount": rec.amount,
                "date": next_due,
                "repeat": True, "category": rec.category or "Uncategorized"
            })

    next_month_expenses.sort(key=lambda e: e["date"])
    next_month_total = sum(e["amount"] for e in next_month_expenses)

    calendar_expenses = defaultdict(list)
    for e in next_month_expenses:
        calendar_expenses[e["date"].day].append(e)

    salary_period_str = f"{period_start.strftime('%d %b %Y')} - {period_end.strftime('%d %b %Y')}"

    health = get_financial_health_score(user_id=uid())
    active_goals = Goal.query.filter_by(status='active', user_id=uid()).limit(3).all()
    recent_notifications = Notification.query.filter_by(is_read=False, user_id=uid()).order_by(
        Notification.created_at.desc()).limit(5).all()

    user = session.get("user", {})
    default_name = user.get("name", "").split()[0] if user.get("name") else "there"
    preferred_name = get_setting("preferred_name", default_name, user_id=uid())

    # ── FIXES ──
    # 1. Net Balance = period only (not carryover)
    net_balance_period = total_income - total_expense

    # 2. Daily budget — clamp to 0 if negative
    daily_budget_safe = max(0, daily_budget_remaining)

    # 3. Savings rate
    savings_rate = round((net_balance_period / total_income * 100), 1) if total_income > 0 else 0

    # 4. Net worth snapshot
    total_investments = sum(inv.current_value for inv in Investment.query.filter_by(user_id=uid(), status='active').all())
    total_loan_outstanding = sum(l.outstanding_balance for l in Loan.query.filter_by(user_id=uid(), loan_status='Active').all())
    net_worth = total_wallet_balance + total_investments - total_loan_outstanding - total_used

    # 5. Friend debts
    from models import Debt
    pending_debts = Debt.query.filter_by(user_id=uid(), status='pending').all()
    owed_to_me = sum(d.amount for d in pending_debts if d.direction == 'lent')
    i_owe = sum(d.amount for d in pending_debts if d.direction == 'owe')

    # 6. Active goals sorted by deadline then % complete
    active_goals_sorted = Goal.query.filter_by(status='active', user_id=uid()).order_by(Goal.target_date).limit(4).all()

    # 7. Daily variable spend calc must only use transactions that have
    # actually occurred — reuses transactions_occurred rather than
    # re-querying the same data under a different name.
    period_transactions = transactions_occurred

    # ── SMART DAILY BUDGET ──
    # Fixed committed = loan payments + card minimums + fixed expenses
    loan_monthly = sum(l.monthly_payment for l in Loan.query.filter_by(user_id=uid(), loan_status='Active').all())
    card_minimums = sum(c.minimum_payment for c in cards)
    fixed_exp_total = sum(f.amount for f in FixedExpense.query.filter_by(user_id=uid()).all())
    recurring_total = sum(r.amount for r in RecurringPayment.query.filter_by(user_id=uid(), is_active=True).all())

    # IMPORTANT — DO NOT PRORATE total_committed to the salary period.
    # Loan EMIs, card minimum payments, and fixed bills are due in FULL
    # regardless of how many days are in the current salary cycle.
    # (e.g. a 25-day period still owes the full monthly EMI, not 25/30 of it)
    total_committed = loan_monthly + card_minimums + fixed_exp_total + recurring_total

    # Free to Spend = period income - full committed costs
    truly_available = total_income - total_committed
    truly_available = max(0, truly_available)

    # Daily variable spend = only non-fixed, non-loan, non-card transactions
    # Exclude categories: Loan Payment, Credit Card, fixed expense names
    fixed_names = {f.name.lower() for f in FixedExpense.query.filter_by(user_id=uid()).all()}
    excluded_cats = {'loan payment', 'credit card', 'loan', 'emi', 'insurance'}

    daily_variable_txns = [
        t for t in period_transactions
        if t.trans_type == 'expense'
        and (t.category or '').lower() not in excluded_cats
        and (t.description or '').lower() not in fixed_names
    ]
    total_variable_spent = sum(t.amount for t in daily_variable_txns)

    # Today's variable spend
    today_variable_spent = sum(
        t.amount for t in daily_variable_txns
        if (t.date if isinstance(t.date, type(today)) else t.date.date()) == today
    )

    # Smart daily budget = truly_available / total days in period
    # Today's remaining = (truly_available - variable_spent_so_far) / days_left
    smart_daily_budget = round(truly_available / max(1, days_total_period), 0)
    smart_today_remaining = round(
        (truly_available - total_variable_spent) / max(1, days_left_in_period), 0
    )
    smart_today_remaining = max(0, smart_today_remaining)

    # Popup — show based on user setting
    show_daily_popup = get_setting('show_daily_popup', 'true', user_id=uid()) == 'true'
    expense_categories = get_user_categories(uid(), "expense")

    return render_template(
        "dashboard.html",
        preferred_name=preferred_name,
        wallets=wallets,
        total_income=total_income,
        expense_categories=expense_categories,
        total_expense=total_expense,
        balance=net_balance_period,
        net_worth=net_worth,
        savings_rate=savings_rate,
        total_investments=total_investments,
        owed_to_me=owed_to_me,
        i_owe=i_owe,
        upcoming_payments=upcoming_fixed_sorted,
        chart_data=chart_data,
        fixed_chart_data=dict(fixed_chart_data),
        monthly_trends=json.dumps({"labels": monthly_labels, "expenses": monthly_values, "income": monthly_income_values}),
        selected_month=(month_str if month_str else today.strftime("%Y-%m")),
        next_month_expenses=next_month_expenses,
        next_month_total=next_month_total,
        total_credit_limit=total_credit_limit,
        total_available_balance=total_available,
        total_used_credit=total_used,
        total_monthly_due=total_monthly_due,
        credit_cards=cards,
        calendar_expenses=calendar_expenses,
        month_start=period_start,
        month_end=period_end,
        period_start=period_start,
        period_end=period_end,
        this_month_transactions=transactions,
        future_txn_ids=future_txn_ids,
        total_wallet_balance=total_wallet_balance,
        salary_period=salary_period_str,
        today=today,
        minimum_required=next_month_total,
        unpaid_fixed_total=unpaid_fixed_total,
        days_left_in_period=days_left_in_period,
        days_total_period=days_total_period,
        days_elapsed=days_elapsed,
        daily_avg_so_far=daily_avg_so_far,
        available_after_fixed=available_after_fixed,
        daily_budget_remaining=daily_budget_safe,
        health=health,
        active_goals=active_goals_sorted,
        recent_notifications=recent_notifications,
        truly_available=truly_available,
        total_committed=total_committed,
        smart_daily_budget=smart_daily_budget,
        smart_today_remaining=smart_today_remaining,
        today_variable_spent=today_variable_spent,
        total_variable_spent=total_variable_spent,
        show_daily_popup=show_daily_popup,
    )


# ─────────────────────────────────────────
#  Transactions
# ─────────────────────────────────────────
@app.route("/transactions", methods=["GET", "POST"])
@login_required
def transactions():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    trans_type = request.args.get("type", "")
    category_filter = request.args.get("category", "")

    query = Transaction.query.filter_by(user_id=uid())

    if start_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(Transaction.date >= start)
        except:
            flash("Invalid start date format", "danger")
    if end_date:
        try:
            end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            query = query.filter(Transaction.date <= end)
        except:
            flash("Invalid end date format", "danger")
    if trans_type:
        query = query.filter(Transaction.trans_type == trans_type)
    if category_filter:
        query = query.filter(Transaction.category.ilike(f"%{category_filter}%"))

    all_txns = query.order_by(Transaction.date.desc()).all()
    categories = db.session.query(Transaction.category).filter(Transaction.user_id == uid()).distinct().all()
    categories = [c[0] for c in categories if c[0]]

    return render_template(
        "all_transactions.html",
        transactions=all_txns,
        start_date=start_date or "",
        end_date=end_date or "",
        type_filter=trans_type,
        category_filter=category_filter,
        categories=categories,
        total=sum(t.amount if t.trans_type == "income" else -t.amount for t in all_txns)
    )


@app.route('/add', methods=['POST'])
@login_required
def add():
    amount, err = validate_amount(request.form.get('amount'), "Amount")
    if err: flash(err, "danger"); return redirect(request.referrer or url_for('dashboard'))

    description, err = validate_text(request.form.get('description'), "Description", max_len=100)
    if err: flash(err, "danger"); return redirect(request.referrer or url_for('dashboard'))

    date_obj, err = validate_date(request.form.get('date'))
    if err: flash(err, "danger"); return redirect(request.referrer or url_for('dashboard'))

    category, _ = validate_text(request.form.get('category', 'General'), "Category", required=False)
    notes, _ = validate_text(request.form.get('notes', ''), "Notes", max_len=500, required=False)
    payment_method = request.form.get('payment_method', '')

    wallet_id, credit_id = None, None
    if payment_method.startswith("wallet_"):
        wallet_id = int(payment_method.split("_")[1])
    elif payment_method.startswith("credit_"):
        credit_id = int(payment_method.split("_")[1])
    elif not payment_method or payment_method == "none":
        # Allow no payment source — cash or untracked
        pass

    add_transaction(
        amount=amount, description=description, trans_type="expense",
        wallet_id=wallet_id, linked_credit=credit_id,
        date_obj=date_obj, category=category or "General",
        notes=notes, user_id=uid()
    )
    flash("Expense added successfully.", "success")
    return redirect(request.referrer or url_for('dashboard'))


@app.route('/delete_transaction/<int:transaction_id>', methods=['POST'])
@login_required
def delete_transaction(transaction_id):
    tr = Transaction.query.filter_by(id=transaction_id, user_id=uid()).first_or_404()
    reverse_transaction_balance(tr)
    desc = tr.description
    db.session.delete(tr)
    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash(f"Transaction '{desc}' deleted and balance restored.", "success")
    return redirect(request.referrer or url_for('dashboard'))


@app.route('/edit_transaction/<int:transaction_id>', methods=['GET', 'POST'])
@login_required
def edit_transaction(transaction_id):
    tr = Transaction.query.filter_by(id=transaction_id, user_id=uid()).first_or_404()
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    cards = CreditCard.query.filter_by(user_id=uid()).all()
    if request.method == 'POST':
        # Reverse whatever balance effect the OLD version of this
        # transaction had, before applying the new values — otherwise
        # changing the amount (or wallet) silently leaves wallet/card
        # balances stale.
        reverse_transaction_balance(tr)

        tr.description = request.form['description']
        tr.amount = float(request.form['amount'])
        tr.category = request.form.get('category', tr.category)
        tr.trans_type = request.form['trans_type']
        tr.notes = request.form.get('notes', '')
        date_str = request.form.get('date')
        if date_str:
            tr.date = datetime.strptime(date_str, '%Y-%m-%d').date()

        new_wallet_id = request.form.get('wallet_id')
        new_wallet_id = int(new_wallet_id) if new_wallet_id else None
        is_due = tr.date <= date.today()

        tr.wallet_id = new_wallet_id if is_due else None
        tr.balance_applied = is_due

        if new_wallet_id and is_due:
            wallet = Wallet.query.filter_by(id=new_wallet_id, user_id=uid()).first()
            if wallet:
                if tr.trans_type == "expense":
                    wallet.balance -= tr.amount
                elif tr.trans_type == "income":
                    wallet.balance += tr.amount

        db.session.commit()
        update_networth_snapshot(user_id=uid())
        flash("Transaction updated and balances adjusted.", "success")
        return redirect(url_for('transactions'))
    expense_categories = get_user_categories(uid(), "expense")
    income_categories = get_user_categories(uid(), "income")
    relevant_categories = income_categories if tr.trans_type == "income" else expense_categories
    matched = next((c for c in relevant_categories if c.name.lower() == (tr.category or "").strip().lower()), None)
    selected_category_id = matched.id if matched else None
    return render_template('edit_transaction.html', transaction=tr, wallets=wallets, cards=cards,
                          expense_categories=expense_categories, income_categories=income_categories,
                          selected_category_id=selected_category_id)


# ─────────────────────────────────────────
#  Income
# ─────────────────────────────────────────
@app.route('/income', methods=['GET', 'POST'])
@login_required
def income():
    if request.method == 'POST':
        amount, err = validate_amount(request.form.get('amount'), "Amount")
        if err: flash(err, "danger"); return redirect(url_for('income'))

        description, err = validate_text(request.form.get('description'), "Description")
        if err: flash(err, "danger"); return redirect(url_for('income'))

        date_obj, err = validate_date(request.form.get('date'))
        if err: flash(err, "danger"); return redirect(url_for('income'))

        category, _ = validate_text(request.form.get('category', 'Income'), required=False)
        notes, _ = validate_text(request.form.get('notes', ''), max_len=500, required=False)
        wallet_id = request.form.get('wallet_id')

        add_transaction(
            amount=amount, description=description, trans_type='income',
            wallet_id=int(wallet_id) if wallet_id else None,
            date_obj=date_obj, category=category or 'Income',
            notes=notes, user_id=uid()
        )
        flash('Income added successfully!', 'success')
        return redirect(url_for('income'))

    income_list = Transaction.query.filter_by(trans_type='income', user_id=uid()).order_by(Transaction.date.desc()).all()
    total_income = sum(i.amount for i in income_list)
    this_month_str = datetime.today().strftime("%Y-%m")
    this_month_income_list = [i for i in income_list if i.date.strftime("%Y-%m") == this_month_str]
    this_month_income_total = sum(i.amount for i in this_month_income_list)
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    income_categories = get_user_categories(uid(), "income")

    return render_template(
        'income.html',
        income_list=income_list,
        total_income=total_income,
        this_month_income_list=this_month_income_list,
        this_month_income_total=this_month_income_total,
        wallets=wallets,
        income_categories=income_categories,
        now=datetime.now
    )


@app.route('/delete_income/<int:income_id>', methods=['POST'])
@login_required
def delete_income(income_id):
    income = Transaction.query.filter_by(id=income_id, user_id=uid()).first_or_404()

    delete_all = request.form.get('delete_related') == 'yes'
    deduct_wallet_id = request.form.get('deduct_wallet_id')

    # Collect amount(s) to deduct before deleting
    if delete_all:
        matching = Transaction.query.filter_by(
            user_id=uid(),
            trans_type='income',
            description=income.description
        ).all()
        total_amount = sum(t.amount for t in matching)
        Transaction.query.filter_by(
            user_id=uid(),
            trans_type='income',
            description=income.description
        ).delete()
    else:
        total_amount = income.amount
        db.session.delete(income)

    # If user chose a wallet to deduct from, subtract the amount
    if deduct_wallet_id:
        wallet = Wallet.query.filter_by(id=int(deduct_wallet_id), user_id=uid()).first()
        if wallet:
            wallet.balance -= total_amount

    db.session.commit()
    update_networth_snapshot(user_id=uid())

    if delete_all:
        flash(f'All income entries for "{income.description}" deleted' + (f' and {total_amount:,.0f} deducted from wallet.' if deduct_wallet_id else '.'), 'info')
    else:
        flash('Income entry deleted' + (f' and {total_amount:,.0f} deducted from wallet.' if deduct_wallet_id else '.'), 'info')
    return redirect(url_for('income'))


# ─────────────────────────────────────────
#  Wallets
# ─────────────────────────────────────────
@app.route("/wallets")
@login_required
def wallets():
    all_wallets = Wallet.query.filter_by(user_id=uid()).all()
    transfers = WalletTransfer.query.filter_by(user_id=uid()).order_by(WalletTransfer.date.desc()).limit(20).all()
    return render_template("wallets.html", wallets=all_wallets, transfers=transfers)


@app.route("/add_wallet", methods=["POST"])
def add_wallet():
    name = request.form["name"]
    wallet_type = request.form["wallet_type"]
    balance = float(request.form["balance"])
    currency = request.form.get("currency", "LKR")
    color = request.form.get("color", "#6366f1")
    notes = request.form.get("notes", "")
    new_wallet = Wallet(name=name, wallet_type=wallet_type, balance=balance,
                        currency=currency, color=color, notes=notes, user_id=uid())
    db.session.add(new_wallet)
    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash("Wallet added!", "success")
    return redirect(url_for("wallets"))


@app.route("/edit_wallet/<int:wallet_id>", methods=["POST"])
def edit_wallet(wallet_id):
    wallet = Wallet.query.filter_by(id=wallet_id, user_id=uid()).first_or_404()
    wallet.name = request.form["name"]
    wallet.wallet_type = request.form["wallet_type"]
    wallet.balance = float(request.form["balance"])
    wallet.currency = request.form.get("currency", "LKR")
    wallet.color = request.form.get("color", wallet.color)
    wallet.notes = request.form.get("notes", "")
    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash("Wallet updated!", "success")
    return redirect(url_for("wallets"))


@app.route("/delete_wallet/<int:wallet_id>", methods=["POST"])
def delete_wallet(wallet_id):
    wallet = Wallet.query.filter_by(id=wallet_id, user_id=uid()).first_or_404()
    # Check if wallet has balance
    if wallet.balance > 0:
        force = request.form.get('force_delete') == 'yes'
        if not force:
            flash(f"Cannot delete {wallet.name} — it has a balance of {wallet.balance:,.0f}. Transfer the balance first or confirm deletion.", "danger")
            return redirect(url_for("wallets"))
    # Check if it's the only wallet
    wallet_count = Wallet.query.filter_by(user_id=uid()).count()
    if wallet_count <= 1:
        flash("Cannot delete your only wallet. Add another wallet first.", "danger")
        return redirect(url_for("wallets"))
    db.session.delete(wallet)
    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash(f"Wallet '{wallet.name}' deleted.", "success")
    return redirect(url_for("wallets"))


@app.route("/transfer_wallet", methods=["POST"])
def transfer_wallet():
    from_id = int(request.form["from_wallet"])
    to_id = int(request.form["to_wallet"])
    amount = float(request.form["amount"])
    note = request.form.get("note", "")

    if from_id == to_id:
        flash("Cannot transfer to the same wallet.", "danger")
        return redirect(url_for("wallets"))

    amount_val, err = validate_amount(request.form.get("amount"), "Transfer amount")
    if err: flash(err, "danger"); return redirect(url_for("wallets"))
    amount = amount_val

    from_wallet = Wallet.query.filter_by(id=from_id, user_id=uid()).first()
    to_wallet = Wallet.query.filter_by(id=to_id, user_id=uid()).first()

    if not from_wallet or not to_wallet:
        flash("Invalid wallet selected.", "danger")
        return redirect(url_for("wallets"))

    if from_wallet.balance < amount:
        flash("Insufficient balance in source wallet.", "danger")
        return redirect(url_for("wallets"))

    from_wallet.balance -= amount
    to_wallet.balance += amount
    transfer = WalletTransfer(from_wallet_id=from_id, to_wallet_id=to_id, amount=amount, note=note, user_id=uid())
    db.session.add(transfer)
    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash("Transfer completed!", "success")
    return redirect(url_for("wallets"))


# ─────────────────────────────────────────
#  Payments — unified page: Fixed Expenses / Recurring / Scheduler
# ─────────────────────────────────────────
def get_combined_upcoming_payments(user_id, month_start, month_end):
    """The single source of truth for 'what's due this period' — combines
    FixedExpense (one-time + repeat_until items), RecurringPayment,
    Loan installments and CreditCard minimums into one sorted list.
    Used by both the Scheduler tab and (kept consistent) anywhere else
    that needs a true 'all upcoming payments' view.

    FixedExpense items that already have a matching paid Transaction
    within this window are excluded — RecurringPayment/Loan/CreditCard
    don't need this check since applying those advances their own
    next_date/next_due_date, which already pushes them out of range."""
    items = []

    def next_occurrence(start_date, after_date):
        d = start_date
        while d < after_date:
            d += relativedelta(months=1)
        return d

    # Names of FixedExpense items already paid within this window, so we
    # don't show something the user already settled as still "due".
    paid_in_window = {
        t.description for t in Transaction.query.filter(
            Transaction.user_id == user_id,
            Transaction.trans_type == "expense",
            Transaction.date >= month_start,
            Transaction.date <= month_end,
        ).all()
    }

    # Fixed expenses (one-time within range, or repeating within range)
    for exp in FixedExpense.query.filter_by(user_id=user_id).all():
        if exp.name in paid_in_window:
            continue
        repeat_until = exp.repeat_until or date.max
        if exp.repeat:
            due = next_occurrence(exp.date, month_start)
            if month_start <= due <= month_end and due <= repeat_until:
                items.append({
                    "id": exp.id, "name": exp.name, "amount": exp.amount,
                    "due_date": due, "category": exp.category or "Uncategorized",
                    "source": "fixed_expense", "recurring": True,
                })
        elif month_start <= exp.date <= month_end:
            items.append({
                "id": exp.id, "name": exp.name, "amount": exp.amount,
                "due_date": exp.date, "category": exp.category or "Uncategorized",
                "source": "fixed_expense", "recurring": False,
            })

    # Recurring payments (the real model — wallet/card linked)
    freq_days = {"weekly": 7, "biweekly": 14, "monthly": None, "quarterly": None, "yearly": None}
    for rec in RecurringPayment.query.filter_by(user_id=user_id, is_active=True).all():
        if rec.end_date and rec.end_date < month_start:
            continue
        due = rec.next_date
        # Step the due date forward into the requested window if needed
        guard = 0
        while due < month_start and guard < 60:
            if rec.frequency == "weekly":
                due += timedelta(weeks=1)
            elif rec.frequency == "biweekly":
                due += timedelta(weeks=2)
            elif rec.frequency == "quarterly":
                due += relativedelta(months=3)
            elif rec.frequency == "yearly":
                due += relativedelta(years=1)
            else:
                due += relativedelta(months=1)
            guard += 1
        if month_start <= due <= month_end and (not rec.end_date or due <= rec.end_date):
            items.append({
                "id": rec.id, "name": rec.name, "amount": rec.amount,
                "due_date": due, "category": rec.category or "Uncategorized",
                "source": "recurring_payment", "recurring": True,
            })

    # Loan installments
    for l in Loan.query.filter_by(user_id=user_id, loan_status="Active").all():
        if l.next_due_date and month_start <= l.next_due_date <= month_end:
            items.append({
                "id": l.id, "name": f"Loan: {l.loan_name}", "amount": l.monthly_payment,
                "due_date": l.next_due_date, "category": "Loan Payment",
                "source": "loan", "recurring": True,
            })

    # Credit card minimum payments
    for c in CreditCard.query.filter_by(user_id=user_id).all():
        if c.due_date and month_start <= c.due_date <= month_end:
            items.append({
                "id": c.id, "name": f"{c.bank_name} Card", "amount": c.minimum_payment,
                "due_date": c.due_date, "category": "Credit Card",
                "source": "credit_card", "recurring": True,
            })

    items.sort(key=lambda x: x["due_date"])
    return items


@app.route('/fixed-expenses')
@login_required
def fixed_expenses():
    today = date.today()
    month_str = request.args.get("month")
    try:
        selected_month = datetime.strptime(month_str, "%Y-%m").date() if month_str else today
    except ValueError:
        selected_month = today

    current_month_start = selected_month.replace(day=1)
    current_month_end = current_month_start + relativedelta(months=1) - timedelta(days=1)
    next_month_start = current_month_start + relativedelta(months=1)
    next_month_end = next_month_start + relativedelta(months=1) - timedelta(days=1)

    def get_next_occurrence(start_date, after_date):
        next_date = start_date
        while next_date < after_date:
            next_date += relativedelta(months=1)
        return next_date

    # Build paid set using salary period so it matches dashboard
    period_start_fe, period_end_fe = get_salary_period(today)
    paid_txns = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.trans_type == "expense",
        Transaction.date >= period_start_fe,
        Transaction.date <= today
    ).all()
    paid_names_fe = set(t.description for t in paid_txns)

    # ── TAB 1: Fixed Expenses (FixedExpense model only) ──
    all_expenses = FixedExpense.query.filter_by(user_id=uid()).order_by(FixedExpense.date).all()
    fixed_recurring, fixed_this_month = [], []
    fixed_chart_data = defaultdict(float)

    for exp in all_expenses:
        repeat_until = exp.repeat_until or date.max
        if not exp.repeat and exp.repeat_until:
            fixed_recurring.append(exp)
        if (exp.repeat or exp.repeat_until) and repeat_until >= current_month_start:
            next_due = get_next_occurrence(exp.date, current_month_start)
            if current_month_start <= next_due <= current_month_end and next_due <= repeat_until:
                fixed_this_month.append({
                    "id": exp.id, "type": exp.name, "amount": exp.amount,
                    "due_date": next_due.strftime('%Y-%m-%d'), "repeat": exp.repeat,
                    "paid": exp.name in paid_names_fe
                })
        elif not exp.repeat and current_month_start <= exp.date <= current_month_end:
            fixed_this_month.append({
                "id": exp.id, "type": exp.name, "amount": exp.amount,
                "due_date": exp.date.strftime('%Y-%m-%d'), "repeat": False,
                "paid": exp.name in paid_names_fe
            })
        if exp.repeat:
            due = get_next_occurrence(exp.date, current_month_start)
            if current_month_start <= due <= current_month_end and due <= repeat_until:
                fixed_chart_data[exp.category or "Uncategorized"] += exp.amount
        elif not exp.repeat and current_month_start <= exp.date <= current_month_end:
            fixed_chart_data[exp.category or "Uncategorized"] += exp.amount

    # ── TAB 2: Recurring (real RecurringPayment model) ──
    recurring_list = RecurringPayment.query.filter_by(user_id=uid()).order_by(RecurringPayment.next_date).all()
    total_recurring_monthly = sum(
        r.amount for r in recurring_list if r.is_active and r.frequency == "monthly"
    )

    # ── TAB 3: Scheduler (everything combined, this period + next period) ──
    # Uses the SALARY PERIOD (not calendar month) so this agrees with the
    # Dashboard's "Next Period Forecast", which is also salary-period based.
    # Previously this tab used calendar-month boundaries while the
    # dashboard used the salary cycle — an item due near a period boundary
    # (e.g. health insurance on the 27th, with a salary cycle starting the
    # 25th) could appear here but be entirely absent from the dashboard's
    # forecast, since the two pages disagreed on what "next period" meant.
    sched_period_start, sched_period_end = get_salary_period(today)
    sched_next_period_start = sched_period_start + relativedelta(months=1)
    sched_next_period_end = sched_next_period_start + relativedelta(months=1) - timedelta(days=1)

    scheduler_this_month = get_combined_upcoming_payments(uid(), sched_period_start, sched_period_end)
    scheduler_next_month = get_combined_upcoming_payments(uid(), sched_next_period_start, sched_next_period_end)

    wallets = Wallet.query.filter_by(user_id=uid()).all()
    credit_cards = CreditCard.query.filter_by(user_id=uid()).all()
    expense_categories = get_user_categories(uid(), "expense")

    return render_template(
        'fixed_expenses.html',
        # Tab 1 — Fixed Expenses
        repeat_until_expenses=fixed_recurring,
        upcoming_fixed=fixed_this_month,
        selected_month=current_month_start.strftime("%Y-%m"),
        current_month=current_month_start,
        month_start=current_month_start,
        month_end=current_month_end,
        fixed_chart_data=dict(fixed_chart_data),
        total_repeat_until=sum(e.amount for e in fixed_recurring),
        total_this_month=sum(e["amount"] for e in fixed_this_month),
        paid_this_month=paid_names_fe,
        relativedelta=relativedelta,
        # Tab 2 — Recurring
        recurring=recurring_list,
        total_recurring_monthly=total_recurring_monthly,
        today=today,
        # Tab 3 — Scheduler
        scheduler_this_month=scheduler_this_month,
        scheduler_next_month=scheduler_next_month,
        scheduler_total_this_month=sum(i["amount"] for i in scheduler_this_month),
        scheduler_total_next_month=sum(i["amount"] for i in scheduler_next_month),
        # Shared
        wallets=wallets,
        credit_cards=credit_cards,
        expense_categories=expense_categories,
    )


@app.route('/add-fixed', methods=['POST'])
@login_required
def add_fixed():
    name = request.form['name']
    category = request.form['category']
    amount = float(request.form['amount'])
    repeat = 'repeat' in request.form
    date_str = request.form['date']
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    notes = request.form.get('notes', '')

    repeat_until_str = request.form.get('repeat_until')
    repeat_until = None
    if repeat_until_str:
        year, month = map(int, repeat_until_str.split('-'))
        last_day = monthrange(year, month)[1]
        repeat_until = date(year, month, last_day)

    name, err = validate_text(name, "Expense name")
    if err: flash(err, "danger"); return redirect(url_for('fixed_expenses'))

    amount_val, err = validate_amount(amount, "Amount")
    if err: flash(err, "danger"); return redirect(url_for('fixed_expenses'))

    new_expense = FixedExpense(
        name=name, category=category or "General", amount=amount_val,
        repeat=repeat, date=date_obj, repeat_until=repeat_until,
        notes=notes, user_id=uid()
    )
    db.session.add(new_expense)
    db.session.commit()
    flash('Fixed expense added!', 'success')
    return redirect(url_for('fixed_expenses'))


@app.route("/apply-fixed/<int:expense_id>", methods=["POST"])
@login_required
def apply_fixed(expense_id):
    fixed_exp = FixedExpense.query.filter_by(id=expense_id, user_id=uid()).first_or_404()
    today = date.today()

    date_str = request.form.get('date')
    paid_date = today
    if date_str:
        try:
            paid_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            paid_date = today

    period_start, _ = get_salary_period(today)

    already = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.description == fixed_exp.name,
        Transaction.trans_type == "expense",
        Transaction.date >= period_start,
        Transaction.date <= today
    ).first()
    if already:
        flash(f"⚠️ '{fixed_exp.name}' already paid this period on {already.date.strftime('%d %b')}. Delete that transaction first to re-apply.", "warning")
        return redirect(request.referrer or url_for("fixed_expenses"))

    payment_method = request.form.get('payment_method')
    wallet_id, credit_id = None, None
    if payment_method and payment_method.startswith("wallet_"):
        wallet_id = int(payment_method.split("_")[1])
    elif payment_method and payment_method.startswith("credit_"):
        credit_id = int(payment_method.split("_")[1])

    add_transaction(
        amount=fixed_exp.amount, description=fixed_exp.name,
        trans_type="expense", wallet_id=wallet_id, linked_credit=credit_id,
        date_obj=paid_date, category=fixed_exp.category, user_id=uid()
    )
    source_note = "record only" if not payment_method else None
    flash(f"✅ {fixed_exp.name} applied" + (f" ({source_note})" if source_note else "") + ".", "success")
    return redirect(request.referrer or url_for("fixed_expenses"))


@app.route('/delete_fixed/<int:expense_id>', methods=['POST'])
@login_required
def delete_fixed(expense_id):
    expense = FixedExpense.query.filter_by(id=expense_id, user_id=uid()).first_or_404()
    db.session.delete(expense)
    db.session.commit()
    flash("Fixed expense deleted.", "success")
    return redirect(request.referrer or url_for('fixed_expenses'))


@app.route("/mark-fixed-paid/<int:expense_id>", methods=["POST"])
@login_required
def mark_fixed_paid(expense_id):
    exp = FixedExpense.query.filter_by(id=expense_id, user_id=uid()).first_or_404()
    today = date.today()
    month_start = today.replace(day=1)

    already = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.description == exp.name,
        Transaction.trans_type == "expense",
        Transaction.date >= month_start,
        Transaction.date <= today
    ).first()
    if already:
        flash(f"⚠️ '{exp.name}' already paid this month on {already.date.strftime('%d %b')}.", "warning")
        return redirect(url_for("fixed_expenses"))

    add_transaction(
        amount=exp.amount, description=exp.name, trans_type="expense",
        date_obj=today, category=exp.category, user_id=uid()
    )
    flash(f"✅ {exp.name} marked as paid.", "success")
    return redirect(url_for("fixed_expenses"))


# ─────────────────────────────────────────
#  Expense Scheduler — retired in favour of the unified Payments page's
#  Scheduler tab, which combines FixedExpense + RecurringPayment + Loan
#  + CreditCard correctly (this route previously omitted RecurringPayment
#  entirely, which was one source of the data mismatches reported).
# ─────────────────────────────────────────
@app.route('/expense-scheduler')
@login_required
def expense_scheduler():
    return redirect(url_for("fixed_expenses") + "?tab=scheduler")


# ─────────────────────────────────────────
#  Categories — shared, real list used everywhere
# ─────────────────────────────────────────
DEFAULT_EXPENSE_CATEGORIES = [
    ("Food & Dining", "🍔", "#f59e0b"),
    ("Transport", "🚗", "#3b82f6"),
    ("Utilities", "💡", "#eab308"),
    ("Entertainment", "🎬", "#ec4899"),
    ("Shopping", "🛍️", "#8b5cf6"),
    ("Health", "🏥", "#ef4444"),
    ("Education", "📚", "#06b6d4"),
    ("Rent", "🏠", "#64748b"),
    ("Insurance", "🛡️", "#0ea5e9"),
    ("Subscription", "📱", "#a855f7"),
]
DEFAULT_INCOME_CATEGORIES = [
    ("Salary", "💼", "#22c55e"),
    ("Freelance", "💻", "#14b8a6"),
    ("Investment", "📈", "#6366f1"),
    ("Gift", "🎁", "#ec4899"),
    ("Other", "🏷️", "#64748b"),
]


def ensure_default_categories(user_id):
    """Seed a user's default category list the first time they need it.
    Safe to call repeatedly — does nothing if already seeded."""
    if not user_id:
        return
    existing = Category.query.filter_by(user_id=user_id).first()
    if existing:
        return
    for name, icon, color in DEFAULT_EXPENSE_CATEGORIES:
        db.session.add(Category(user_id=user_id, name=name, kind="expense", icon=icon, color=color, is_default=True))
    for name, icon, color in DEFAULT_INCOME_CATEGORIES:
        db.session.add(Category(user_id=user_id, name=name, kind="income", icon=icon, color=color, is_default=True))
    db.session.commit()


def get_user_categories(user_id, kind="expense"):
    ensure_default_categories(user_id)
    return Category.query.filter_by(user_id=user_id, kind=kind).order_by(Category.name).all()


@app.route("/categories/list")
@login_required
def categories_list():
    """JSON list of the current user's categories, for populating dropdowns
    and for the inline 'add new category' flow without a page reload."""
    kind = request.args.get("kind", "expense")
    cats = get_user_categories(uid(), kind)
    return jsonify([
        {"id": c.id, "name": c.name, "icon": c.icon, "color": c.color}
        for c in cats
    ])


@app.route("/categories/add", methods=["POST"])
@login_required
def categories_add():
    """Create a new category on the fly (used by the '+ Add category'
    option inside the dropdown). Returns the new category as JSON so
    the dropdown can immediately select it without reloading."""
    name = (request.form.get("name") or "").strip()
    kind = request.form.get("kind", "expense")
    icon = (request.form.get("icon") or "🏷️").strip()
    color = (request.form.get("color") or "#6366f1").strip()

    if not name:
        return jsonify({"error": "Category name is required"}), 400

    existing = Category.query.filter_by(user_id=uid(), name=name, kind=kind).first()
    if existing:
        return jsonify({"id": existing.id, "name": existing.name, "icon": existing.icon, "color": existing.color})

    cat = Category(user_id=uid(), name=name, kind=kind, icon=icon, color=color, is_default=False)
    db.session.add(cat)
    db.session.commit()
    return jsonify({"id": cat.id, "name": cat.name, "icon": cat.icon, "color": cat.color})


@app.route("/categories/delete/<int:cat_id>", methods=["POST"])
@login_required
def categories_delete(cat_id):
    cat = Category.query.filter_by(id=cat_id, user_id=uid()).first_or_404()
    if cat.is_default:
        flash("Default categories can't be deleted.", "danger")
        return redirect(request.referrer or url_for("dashboard"))
    db.session.delete(cat)
    db.session.commit()
    flash(f"Category '{cat.name}' deleted.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/categories/repair-mismatches")
@login_required
def categories_repair_mismatches():
    """One-time helper: finds budgets whose category name has zero
    matching transactions this month, and transactions whose category
    doesn't match any budget — surfaces them so the user can fix the
    exact mismatch (e.g. budget says 'Wi-Fi', transaction says 'Wifi')."""
    today = date.today()
    month_start = today.replace(day=1)
    month_end = month_start + relativedelta(months=1) - timedelta(days=1)

    budgets = BudgetPlanner.query.filter_by(user_id=uid(), month=today.strftime("%Y-%m")).all()
    txns = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.trans_type == "expense",
        Transaction.date >= month_start,
        Transaction.date <= month_end,
    ).all()

    txn_categories = {(t.category or "").strip().lower() for t in txns if t.category}
    mismatches = []
    for b in budgets:
        b_cat_lower = (b.category or "").strip().lower()
        if b_cat_lower not in txn_categories:
            # Look for a close-ish match to suggest (same first word, etc.)
            suggestions = [c for c in txn_categories if b_cat_lower.split()[0] in c or c.split()[0] in b_cat_lower] if b_cat_lower else []
            mismatches.append({"budget_id": b.id, "budget_category": b.category, "suggestions": suggestions})

    return render_template("category_repair.html", mismatches=mismatches, txn_categories=sorted(txn_categories))


@app.route("/categories/repair-mismatches/fix", methods=["POST"])
@login_required
def categories_repair_fix():
    """Apply a fix chosen on the repair page: either rename the budget's
    category to match an existing transaction category, or rename all
    matching transactions to match the budget's category."""
    budget_id = request.form.get("budget_id")
    action = request.form.get("action")  # "rename_budget" or "rename_transactions"
    target_category = (request.form.get("target_category") or "").strip()

    budget = BudgetPlanner.query.filter_by(id=budget_id, user_id=uid()).first_or_404()

    if action == "rename_budget" and target_category:
        old_name = budget.category
        budget.category = target_category
        db.session.commit()
        flash(f"Budget category renamed from '{old_name}' to '{target_category}'.", "success")

    elif action == "rename_transactions" and target_category:
        # Rename every transaction this month whose category loosely
        # matched the suggestion, to the budget's exact category name
        today = date.today()
        month_start = today.replace(day=1)
        month_end = month_start + relativedelta(months=1) - timedelta(days=1)
        updated = Transaction.query.filter(
            Transaction.user_id == uid(),
            Transaction.trans_type == "expense",
            Transaction.date >= month_start,
            Transaction.date <= month_end,
            Transaction.category.ilike(target_category),
        ).update({"category": budget.category}, synchronize_session=False)
        db.session.commit()
        flash(f"Renamed {updated} transaction(s) to '{budget.category}'.", "success")

    return redirect(url_for("categories_repair_mismatches"))


# ─────────────────────────────────────────
#  Budget Planner
# ─────────────────────────────────────────
@app.route("/budget_planner", methods=["GET", "POST"])
@login_required
def budget_planner():
    today = date.today()
    month_str = request.args.get("month", today.strftime("%Y-%m"))
    current_month = month_str

    if request.method == "POST":
        category = request.form["category"]
        amount = float(request.form["amount"])
        existing = BudgetPlanner.query.filter_by(category=category, month=current_month, user_id=uid()).first()
        if existing:
            existing.amount = amount
        else:
            budget = BudgetPlanner(category=category, amount=amount, month=current_month, user_id=uid())
            db.session.add(budget)
        db.session.commit()
        flash("Budget saved!", "success")
        return redirect(url_for("budget_planner", month=current_month))

    try:
        month_date = datetime.strptime(current_month, "%Y-%m").date()
    except:
        month_date = today.replace(day=1)

    month_start = month_date.replace(day=1)
    month_end = month_start + relativedelta(months=1) - timedelta(days=1)

    budgets = BudgetPlanner.query.filter_by(month=current_month, user_id=uid()).all()
    transactions = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.trans_type == "expense",
        Transaction.date >= month_start,
        Transaction.date <= month_end
    ).all()

    spending_by_category = defaultdict(float)
    for t in transactions:
        cat = t.category or t.description
        spending_by_category[cat] += t.amount

    budget_summary = []
    for b in budgets:
        # Match by exact category OR partial match (case-insensitive)
        spent = spending_by_category.get(b.category, 0)
        if spent == 0:
            for cat, amt in spending_by_category.items():
                if cat and b.category and cat.lower() == b.category.lower():
                    spent += amt
        remaining = b.amount - spent
        pct = min(100, (spent / b.amount * 100)) if b.amount > 0 else 0
        budget_summary.append({
            "id": b.id,
            "category": b.category,
            "budget": b.amount,
            "spent": spent,
            "remaining": remaining,
            "pct": pct
        })

    total_budgeted = sum(b.amount for b in budgets)
    total_spent = sum(s["spent"] for s in budget_summary)

    expense_categories = get_user_categories(uid(), "expense")

    return render_template(
        "budget_planner.html",
        budget_summary=budget_summary,
        budgets=budgets,
        month=current_month,
        total_budgeted=total_budgeted,
        total_spent=total_spent,
        expense_categories=expense_categories,
    )


@app.route("/delete_budget/<int:budget_id>", methods=["POST"])
@login_required
def delete_budget(budget_id):
    b = BudgetPlanner.query.filter_by(id=budget_id, user_id=uid()).first_or_404()
    db.session.delete(b)
    db.session.commit()
    flash("Budget deleted.", "success")
    return redirect(url_for("budget_planner"))


# ─────────────────────────────────────────
#  Credit Cards
# ─────────────────────────────────────────
@app.route("/credit_cards", methods=["GET"])
@login_required
def credit_cards():
    cards = CreditCard.query.filter_by(user_id=uid()).all()
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    card_colors = [
        ("#00416a", "#0f2027"), ("#8e2de2", "#4a00e0"), ("#ff416c", "#ff4b2b"),
        ("#11998e", "#38ef7d"), ("#f7971e", "#ffd200"),
    ]
    return render_template(
        "credit_cards.html",
        credit_cards=cards,
        wallets=wallets,
        total_credit_limit=sum(c.credit_limit for c in cards),
        total_available_balance=sum(c.available_balance for c in cards),
        total_used_credit=sum(c.credit_limit - c.available_balance for c in cards),
        card_colors=card_colors,
        total_monthly_due=sum(c.minimum_payment for c in cards),
    )


@app.route("/add-credit-card", methods=["POST"])
def add_credit_card():
    bank_name, err = validate_text(request.form.get('bank_name'), "Bank name")
    if err: flash(err, "danger"); return redirect(url_for("credit_cards"))

    credit_limit, err = validate_amount(request.form.get('credit_limit'), "Credit limit")
    if err: flash(err, "danger"); return redirect(url_for("credit_cards"))

    available, err = validate_amount(request.form.get('available_balance'), "Available balance")
    if err: flash(err, "danger"); return redirect(url_for("credit_cards"))

    min_payment, err = validate_amount(request.form.get('minimum_payment'), "Minimum payment")
    if err: flash(err, "danger"); return redirect(url_for("credit_cards"))

    due_date, err = validate_date(request.form.get('due_date'), "Due date")
    if err: flash(err, "danger"); return redirect(url_for("credit_cards"))

    if available > credit_limit:
        flash("Available balance cannot exceed credit limit.", "danger")
        return redirect(url_for("credit_cards"))

    card = CreditCard(
        bank_name=bank_name,
        card_number=request.form.get('card_number', '')[:4],
        credit_limit=credit_limit, available_balance=available,
        minimum_payment=min_payment, due_date=due_date,
        interest_rate=float(request.form.get('interest_rate') or 0),
        notes=request.form.get('notes', ''), user_id=uid()
    )
    db.session.add(card)
    db.session.commit()
    flash("Credit card added!", "success")
    return redirect(url_for("credit_cards"))


@app.route("/edit-credit-card/<int:card_id>", methods=["POST"])
def edit_credit_card(card_id):
    card = CreditCard.query.filter_by(id=card_id, user_id=uid()).first_or_404()
    card.bank_name = request.form['bank_name']
    card.card_number = request.form['card_number']
    card.credit_limit = float(request.form['credit_limit'])
    card.available_balance = float(request.form['available_balance'])
    card.minimum_payment = float(request.form['minimum_payment'])
    card.due_date = datetime.strptime(request.form['due_date'], "%Y-%m-%d").date()
    card.interest_rate = float(request.form.get('interest_rate', 0))
    card.notes = request.form.get('notes', '')
    db.session.commit()
    flash("Credit card updated!", "success")
    return redirect(url_for("credit_cards"))


@app.route("/pay-credit-card/<int:card_id>", methods=["POST"])
@login_required
def pay_credit_card(card_id):
    card = CreditCard.query.filter_by(id=card_id, user_id=uid()).first_or_404()
    try:
        amount = float(request.form.get('amount', card.minimum_payment))
        if amount <= 0:
            flash("Payment amount must be greater than zero.", "danger")
            return redirect(request.referrer or url_for("credit_cards"))
    except (ValueError, TypeError):
        flash("Invalid payment amount.", "danger")
        return redirect(request.referrer or url_for("credit_cards"))

    wallet_id = request.form.get('wallet_id')
    add_transaction(
        amount=amount,
        description=f"{card.bank_name} Card Payment",
        trans_type="expense",
        wallet_id=int(wallet_id) if wallet_id else None,
        date_obj=date.today(),
        category="Credit Card Payment",
        user_id=uid()
    )
    # Restore available balance
    card.available_balance = min(card.credit_limit, card.available_balance + amount)
    db.session.commit()
    update_networth_snapshot(user_id=uid())
    sym = get_setting("currency_symbol", "LKR", user_id=uid())
    flash(f"✅ Payment of {sym} {amount:,.0f} recorded. Available balance updated.", "success")
    return redirect(request.referrer or url_for("credit_cards"))


@app.route("/delete-credit-card/<int:card_id>", methods=["POST"])
def delete_credit_card(card_id):
    card = CreditCard.query.filter_by(id=card_id, user_id=uid()).first_or_404()
    db.session.delete(card)
    db.session.commit()
    flash("Credit card deleted!", "success")
    return redirect(url_for("credit_cards"))


# ─────────────────────────────────────────
#  Loans
# ─────────────────────────────────────────
@app.route('/loan', methods=['GET'])
@login_required
def loan_list():
    loans = Loan.query.filter_by(user_id=uid()).all()
    today = date.today()
    loan_data = []
    for loan in loans:
        paid = loan.principal_amount - loan.outstanding_balance
        progress = (paid / loan.principal_amount * 100) if loan.principal_amount > 0 else 0
        months_remaining = 0
        if loan.monthly_payment > 0 and loan.outstanding_balance > 0:
            monthly_rate = loan.interest_rate / 100 / 12
            if monthly_rate > 0:
                import math
                try:
                    months_remaining = math.ceil(
                        -math.log(1 - (loan.outstanding_balance * monthly_rate) / loan.monthly_payment)
                        / math.log(1 + monthly_rate)
                    )
                except (ValueError, ZeroDivisionError):
                    months_remaining = int(loan.outstanding_balance / loan.monthly_payment)
            else:
                months_remaining = int(loan.outstanding_balance / loan.monthly_payment)
        overdue = loan.next_due_date < today if loan.next_due_date else False
        loan_data.append({
            "loan": loan, "paid": paid, "progress": progress,
            "months_remaining": months_remaining, "overdue": overdue
        })
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    return render_template("loan.html", loans=loans, loan_data=loan_data, wallets=wallets)


@app.route('/loan/add', methods=['POST'])
def add_loan():
    loan_name, err = validate_text(request.form.get('loan_name'), "Loan name")
    if err: flash(err, "danger"); return redirect(url_for('loan_list'))

    lender_name, err = validate_text(request.form.get('lender_name'), "Lender name")
    if err: flash(err, "danger"); return redirect(url_for('loan_list'))

    principal, err = validate_amount(request.form.get('principal_amount'), "Principal amount")
    if err: flash(err, "danger"); return redirect(url_for('loan_list'))

    outstanding, err = validate_amount(request.form.get('outstanding_balance'), "Outstanding balance")
    if err: flash(err, "danger"); return redirect(url_for('loan_list'))

    monthly, err = validate_amount(request.form.get('monthly_payment'), "Monthly payment")
    if err: flash(err, "danger"); return redirect(url_for('loan_list'))

    start_date, err = validate_date(request.form.get('start_date'), "Start date")
    if err: flash(err, "danger"); return redirect(url_for('loan_list'))

    next_due, err = validate_date(request.form.get('next_due_date'), "Next due date")
    if err: flash(err, "danger"); return redirect(url_for('loan_list'))

    try:
        interest_rate = float(request.form.get('interest_rate', 0))
        loan_term = int(request.form.get('loan_term', 12))
    except ValueError:
        flash("Interest rate and loan term must be numbers.", "danger")
        return redirect(url_for('loan_list'))

    loan = Loan(
        loan_name=loan_name, lender_name=lender_name,
        principal_amount=principal, interest_rate=interest_rate,
        loan_term=loan_term, start_date=start_date,
        monthly_payment=monthly, payment_frequency='Monthly',
        next_due_date=next_due, outstanding_balance=outstanding,
        loan_status=request.form.get('loan_status', 'Active'),
        notes=request.form.get('notes'), user_id=uid()
    )
    db.session.add(loan)
    db.session.commit()
    flash("Loan added!", "success")
    return redirect(url_for('loan_list'))


@app.route('/loan/pay/<int:loan_id>', methods=['POST'])
def pay_loan(loan_id):
    loan = Loan.query.filter_by(id=loan_id, user_id=uid()).first_or_404()
    amount = float(request.form.get('amount', loan.monthly_payment))
    wallet_id = request.form.get('wallet_id')

    add_transaction(
        amount=amount,
        description=f"Loan payment: {loan.loan_name}",
        trans_type="expense",
        wallet_id=int(wallet_id) if wallet_id else None,
        linked_loan=loan_id,
        date_obj=date.today(),
        category="Loan Payment",
        user_id=uid()
    )
    loan.next_due_date = loan.next_due_date + relativedelta(months=1)
    if loan.outstanding_balance <= 0:
        loan.loan_status = "Paid Off"
    db.session.commit()
    flash(f"Payment of LKR {amount:,.2f} recorded for {loan.loan_name}.", "success")
    return redirect(url_for('loan_list'))


@app.route('/loan/edit/<int:loan_id>', methods=['POST'])
def edit_loan(loan_id):
    loan = Loan.query.filter_by(id=loan_id, user_id=uid()).first_or_404()
    loan.loan_name = request.form['loan_name']
    loan.lender_name = request.form['lender_name']
    loan.principal_amount = float(request.form['principal_amount'])
    loan.interest_rate = float(request.form['interest_rate'])
    loan.loan_term = int(request.form['loan_term'])
    loan.start_date = datetime.strptime(request.form['start_date'], "%Y-%m-%d").date()
    loan.monthly_payment = float(request.form['monthly_payment'])
    loan.next_due_date = datetime.strptime(request.form['next_due_date'], "%Y-%m-%d").date()
    loan.outstanding_balance = float(request.form['outstanding_balance'])
    loan.loan_status = request.form['loan_status']
    loan.notes = request.form.get('notes')
    db.session.commit()
    flash('Loan updated!', 'success')
    return redirect(url_for('loan_list'))


@app.route('/loan/delete/<int:loan_id>', methods=['POST'])
def delete_loan(loan_id):
    loan = Loan.query.filter_by(id=loan_id, user_id=uid()).first_or_404()
    db.session.delete(loan)
    db.session.commit()
    flash('Loan deleted!', 'success')
    return redirect(url_for('loan_list'))


# ─────────────────────────────────────────
#  Net Worth
# ─────────────────────────────────────────
@app.route("/net-worth-tracker")
@login_required
def net_worth_tracker():
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    total_assets = sum(w.balance for w in wallets)
    loans = Loan.query.filter_by(user_id=uid()).all()
    total_loans = sum(l.outstanding_balance for l in loans)
    cards = CreditCard.query.filter_by(user_id=uid()).all()
    total_cards = sum(c.credit_limit - c.available_balance for c in cards)
    total_liabilities = total_loans + total_cards
    net_worth = total_assets - total_liabilities

    today = date.today()
    existing = NetWorthHistory.query.filter_by(date=today, user_id=uid()).first()
    if not existing:
        snapshot = NetWorthHistory(
            date=today, total_assets=total_assets,
            total_liabilities=total_liabilities, net_worth=net_worth,
            user_id=uid()
        )
        db.session.add(snapshot)
        db.session.commit()

    history = NetWorthHistory.query.filter_by(user_id=uid()).order_by(NetWorthHistory.date).all()
    dates = [h.date.strftime("%Y-%m-%d") for h in history]
    net_worth_values = [h.net_worth for h in history]
    assets_values = [h.total_assets for h in history]
    liab_values = [h.total_liabilities for h in history]

    return render_template(
        "net_worth_tracker.html",
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        net_worth=net_worth,
        history=history,
        dates=dates,
        net_worth_values=net_worth_values,
        assets_values=assets_values,
        liab_values=liab_values,
        wallets=wallets,
        loans=loans,
        cards=cards
    )


# ─────────────────────────────────────────
#  Goals (NEW)
# ─────────────────────────────────────────
@app.route("/goals", methods=["GET", "POST"])
@login_required
def goals():
    if request.method == "POST":
        target_date_str = request.form.get('target_date')
        name, err = validate_text(request.form.get('name'), "Goal name")
        if err: flash(err, "danger"); return redirect(url_for("goals"))

        target_amount, err = validate_amount(request.form.get('target_amount'), "Target amount")
        if err: flash(err, "danger"); return redirect(url_for("goals"))

        try:
            current_amount = float(request.form.get('current_amount') or 0)
            if current_amount < 0:
                flash("Current amount cannot be negative.", "danger")
                return redirect(url_for("goals"))
        except ValueError:
            flash("Current amount must be a number.", "danger")
            return redirect(url_for("goals"))

        target_date = None
        if target_date_str:
            target_date, err = validate_date(target_date_str, "Target date")
            if err: flash(err, "danger"); return redirect(url_for("goals"))

        goal = Goal(
            name=name,
            description=request.form.get('description', '')[:300],
            target_amount=target_amount,
            current_amount=current_amount,
            target_date=target_date,
            category=request.form.get('category', 'Savings'),
            icon=request.form.get('icon', '🎯')[:4],
            color=request.form.get('color', '#6366f1'),
            wallet_id=int(request.form['wallet_id']) if request.form.get('wallet_id') else None,
            user_id=uid()
        )
        db.session.add(goal)
        db.session.commit()
        flash("Goal created!", "success")
        return redirect(url_for("goals"))

    all_goals = Goal.query.filter_by(user_id=uid()).order_by(Goal.status, Goal.target_date).all()
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    return render_template("goals.html", goals=all_goals, wallets=wallets)


@app.route("/goals/contribute/<int:goal_id>", methods=["POST"])
def contribute_goal(goal_id):
    goal = Goal.query.filter_by(id=goal_id, user_id=uid()).first_or_404()

    amount, err = validate_amount(request.form.get('amount'), "Contribution amount")
    if err: flash(err, "danger"); return redirect(url_for("goals"))

    wallet_id = request.form.get('wallet_id')
    goal.current_amount += amount
    if goal.current_amount >= goal.target_amount:
        goal.status = "completed"
        flash(f"🎉 Goal '{goal.name}' completed!", "success")
    else:
        flash(f"LKR {amount:,.2f} added to '{goal.name}'.", "success")

    if wallet_id:
        wallet = Wallet.query.filter_by(id=int(wallet_id), user_id=uid()).first()
        if wallet:
            wallet.balance -= amount

    db.session.commit()
    return redirect(url_for("goals"))


@app.route("/goals/delete/<int:goal_id>", methods=["POST"])
def delete_goal(goal_id):
    goal = Goal.query.filter_by(id=goal_id, user_id=uid()).first_or_404()
    db.session.delete(goal)
    db.session.commit()
    flash("Goal deleted.", "info")
    return redirect(url_for("goals"))


@app.route("/goals/status/<int:goal_id>", methods=["POST"])
def toggle_goal_status(goal_id):
    goal = Goal.query.filter_by(id=goal_id, user_id=uid()).first_or_404()
    new_status = request.form.get('status', 'active')
    goal.status = new_status
    db.session.commit()
    flash(f"Goal status updated to {new_status}.", "success")
    return redirect(url_for("goals"))


# ─────────────────────────────────────────
#  Recurring Payments (NEW)
# ─────────────────────────────────────────
@app.route("/recurring", methods=["GET", "POST"])
@login_required
def recurring_payments():
    if request.method == "POST":
        end_date_str = request.form.get('end_date')
        rec = RecurringPayment(
            name=request.form['name'],
            amount=float(request.form['amount']),
            frequency=request.form['frequency'],
            next_date=datetime.strptime(request.form['next_date'], "%Y-%m-%d").date(),
            end_date=datetime.strptime(end_date_str, "%Y-%m-%d").date() if end_date_str else None,
            category=request.form.get('category', 'General'),
            wallet_id=int(request.form['wallet_id']) if request.form.get('wallet_id') else None,
            credit_card_id=int(request.form['credit_card_id']) if request.form.get('credit_card_id') else None,
            auto_apply=('auto_apply' in request.form),
            user_id=uid()
        )
        db.session.add(rec)
        db.session.commit()
        flash("Recurring payment added!", "success")
        return redirect(url_for("fixed_expenses") + "?tab=recurring")

    # GET /recurring now redirects into the unified Payments page —
    # the standalone recurring.html view has been retired in favour of
    # the "Recurring" tab there, which shows the exact same data.
    return redirect(url_for("fixed_expenses") + "?tab=recurring")


@app.route("/recurring/delete/<int:rec_id>", methods=["POST"])
@login_required
def delete_recurring(rec_id):
    rec = RecurringPayment.query.filter_by(id=rec_id, user_id=uid()).first_or_404()
    db.session.delete(rec)
    db.session.commit()
    flash("Recurring payment deleted.", "info")
    return redirect(url_for("fixed_expenses") + "?tab=recurring")


@app.route("/recurring/apply/<int:rec_id>", methods=["POST"])
@login_required
def apply_recurring(rec_id):
    rec = RecurringPayment.query.filter_by(id=rec_id, user_id=uid()).first_or_404()
    freq_map = {
        "weekly": timedelta(weeks=1), "biweekly": timedelta(weeks=2),
        "monthly": relativedelta(months=1), "quarterly": relativedelta(months=3),
        "yearly": relativedelta(years=1),
    }
    add_transaction(
        amount=rec.amount, description=rec.name, trans_type="expense",
        wallet_id=rec.wallet_id, linked_credit=rec.credit_card_id,
        date_obj=date.today(), category=rec.category, user_id=uid()
    )
    rec.last_applied = date.today()
    rec.next_date = rec.next_date + freq_map.get(rec.frequency, relativedelta(months=1))
    db.session.commit()
    flash(f"{rec.name} applied.", "success")
    return redirect(url_for("fixed_expenses") + "?tab=recurring")


# ─────────────────────────────────────────
#  Analytics (NEW)
# ─────────────────────────────────────────
@app.route("/analytics")
@login_required
def analytics():
    today = date.today()
    period = request.args.get("period", "month")

    # FIX: Use salary period for "month" to be consistent with dashboard
    if period == "week":
        start = today - timedelta(days=7)
    elif period == "quarter":
        start = today - relativedelta(months=3)
    elif period == "year":
        start = today - relativedelta(years=1)
    else:
        # Use salary period start instead of calendar month
        period_start, period_end = get_salary_period(today)
        start = period_start

    insights = get_spending_insights(start, today, user_id=uid())
    health = get_financial_health_score(user_id=uid())

    # FIX: Add user_id filter to monthly query
    monthly_data = []
    for i in range(11, -1, -1):
        m_start = (today - relativedelta(months=i)).replace(day=1)
        m_end = m_start + relativedelta(months=1) - timedelta(days=1)
        txns = Transaction.query.filter(
            Transaction.user_id == uid(),
            Transaction.date >= m_start,
            Transaction.date <= m_end
        ).all()
        income = sum(t.amount for t in txns if t.trans_type == "income")
        expense = sum(t.amount for t in txns if t.trans_type == "expense")
        monthly_data.append({
            "month": m_start.strftime("%b %Y"),
            "income": income,
            "expense": expense,
            "savings": income - expense,
            "savings_rate": round((income - expense) / income * 100, 1) if income > 0 else 0
        })

    # FIX: Top categories respect selected period
    cat_txns = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.trans_type == "expense",
        Transaction.date >= start,
        Transaction.date <= today
    ).all()
    cat_totals = defaultdict(float)
    for t in cat_txns:
        cat_totals[t.category or "Other"] += t.amount
    top_categories = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)[:8]

    # Income by category for the period
    inc_txns = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.trans_type == "income",
        Transaction.date >= start,
        Transaction.date <= today
    ).all()
    income_by_cat = defaultdict(float)
    total_income_period = 0
    for t in inc_txns:
        income_by_cat[t.category or "Salary"] += t.amount
        total_income_period += t.amount
    top_income_sources = sorted(income_by_cat.items(), key=lambda x: x[1], reverse=True)[:6]

    # FIX: Weekday vs Weekend — avg per actual day count, not per transaction
    all_txns_period = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.trans_type == "expense",
        Transaction.date >= start,
        Transaction.date <= today
    ).all()
    weekday_total = sum(t.amount for t in all_txns_period
        if (t.date.date() if isinstance(t.date, datetime) else t.date).weekday() < 5)
    weekend_total = sum(t.amount for t in all_txns_period
        if (t.date.date() if isinstance(t.date, datetime) else t.date).weekday() >= 5)

    # Count actual weekdays/weekends in period
    num_days = (today - start).days + 1
    weekday_days = sum(1 for i in range(num_days) if (start + timedelta(days=i)).weekday() < 5)
    weekend_days = max(1, num_days - weekday_days)
    weekday_avg = weekday_total / max(1, weekday_days)
    weekend_avg = weekend_total / max(1, weekend_days)

    # Savings rate for period
    total_expense_period = sum(t.amount for t in all_txns_period)
    savings_rate = round((total_income_period - total_expense_period) / total_income_period * 100, 1) if total_income_period > 0 else 0

    # FIX: fixed_committed includes recurring + fixed expenses too
    loans_active = Loan.query.filter_by(user_id=uid(), loan_status='Active').all()
    cards_active = CreditCard.query.filter_by(user_id=uid()).all()
    recurring_active = RecurringPayment.query.filter_by(user_id=uid(), is_active=True).all()
    fixed_all = FixedExpense.query.filter_by(user_id=uid()).filter(
        FixedExpense.repeat == True
    ).all()
    fixed_committed = (
        sum(l.monthly_payment for l in loans_active) +
        sum(c.minimum_payment for c in cards_active) +
        sum(r.amount for r in recurring_active) +
        sum(f.amount for f in fixed_all)
    )

    # Largest single expenses in period
    largest_expenses = sorted(
        [t for t in all_txns_period],
        key=lambda x: x.amount, reverse=True
    )[:5]

    # Budget vs actual comparison
    budgets = BudgetPlanner.query.filter_by(user_id=uid()).all()
    budget_vs_actual = []
    for b in budgets:
        actual = cat_totals.get(b.category, 0)
        budget_vs_actual.append({
            "category": b.category,
            "budget": b.amount,
            "actual": actual,
            "pct": round(actual / b.amount * 100) if b.amount > 0 else 0
        })

    widgets = get_analytics_widgets(user_id=uid())

    return render_template(
        "analytics.html",
        insights=insights,
        health=health,
        monthly_data=monthly_data,
        top_categories=top_categories,
        top_income_sources=top_income_sources,
        total_income_period=total_income_period,
        savings_rate=savings_rate,
        period=period,
        start_date=start,
        end_date=today,
        weekday_avg=weekday_avg,
        weekend_avg=weekend_avg,
        weekday_total=weekday_total,
        weekend_total=weekend_total,
        fixed_committed=fixed_committed,
        largest_expenses=largest_expenses,
        budget_vs_actual=budget_vs_actual,
        widgets=widgets,
    )


# ─────────────────────────────────────────
#  Notifications (NEW)
# ─────────────────────────────────────────
@app.route("/notifications")
@login_required
def notifications():
    # Clean up old read notifications older than 30 days
    cutoff = datetime.utcnow() - timedelta(days=30)
    Notification.query.filter(
        Notification.user_id == uid(),
        Notification.is_read == True,
        Notification.created_at < cutoff
    ).delete()
    db.session.commit()

    check_and_create_notifications(user_id=uid())
    all_notifs = Notification.query.filter_by(user_id=uid()).order_by(
        Notification.is_read.asc(),
        Notification.created_at.desc()
    ).all()
    unread = [n for n in all_notifs if not n.is_read]
    read = [n for n in all_notifs if n.is_read]
    return render_template("notifications.html", notifications=all_notifs, unread=unread, read_notifs=read)


@app.route("/notifications/read/<int:notif_id>", methods=["POST"])
def mark_notification_read(notif_id):
    n = Notification.query.filter_by(id=notif_id, user_id=uid()).first_or_404()
    n.is_read = True
    db.session.commit()
    flash("Notification marked as read.", "success")
    return redirect(request.referrer or url_for("notifications"))


@app.route("/notifications/read-all", methods=["POST"])
def mark_all_read():
    Notification.query.filter_by(user_id=uid()).update({"is_read": True})
    db.session.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("notifications"))


# ─────────────────────────────────────────
#  Settings (NEW)
# ─────────────────────────────────────────
@app.route("/toggle-dark-mode", methods=["POST"])
@login_required
def toggle_dark_mode():
    current = get_setting("dark_mode", "false", user_id=uid())
    new_val = "false" if current == "true" else "true"
    set_setting("dark_mode", new_val, user_id=uid())
    return jsonify({"dark_mode": new_val == "true"})


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        u = uid()
        set_setting("salary_cycle_day", request.form.get("salary_cycle_day", "25"), user_id=u)
        set_setting("currency_symbol", request.form.get("currency_symbol", "LKR"), user_id=u)
        # dark_mode is now toggled instantly via /toggle-dark-mode AJAX endpoint
        # (no longer submitted as part of this form)
        preferred_name = request.form.get("preferred_name", "").strip()
        if preferred_name:
            set_setting("preferred_name", preferred_name, user_id=u)
        # Daily popup preference
        show_popup = request.form.get("show_daily_popup", "off")
        set_setting("show_daily_popup", "true" if show_popup == "on" else "false", user_id=u)
        # Monthly email preference
        monthly_email = request.form.get("monthly_email", "off")
        set_setting("monthly_email", "true" if monthly_email == "on" else "false", user_id=u)
        db.session.commit()  # safety commit in case any setting wasn't auto-committed
        flash("Settings saved!", "success")
        return redirect(url_for("settings"))

    user = session.get("user", {})
    user_id = uid()
    return render_template("settings.html",
                           salary_cycle_day=get_setting("salary_cycle_day", "25", user_id=user_id),
                           currency_symbol=get_setting("currency_symbol", "LKR", user_id=user_id),
                           dark_mode=get_setting("dark_mode", "false", user_id=user_id) == "true",
                           preferred_name=get_setting("preferred_name", user.get("name","").split()[0] if user.get("name") else "", user_id=user_id),
                           show_daily_popup=get_setting("show_daily_popup", "true", user_id=user_id) == "true",
                           monthly_email=get_setting("monthly_email", "true", user_id=user_id) == "true",
                           user=user)


# ─────────────────────────────────────────
#  Export (NEW)
# ─────────────────────────────────────────
@app.route("/export/transactions")
def export_transactions():
    start_str = request.args.get("start")
    end_str = request.args.get("end")
    query = Transaction.query.filter_by(user_id=uid())
    if start_str:
        query = query.filter(Transaction.date >= datetime.strptime(start_str, "%Y-%m-%d"))
    if end_str:
        query = query.filter(Transaction.date <= datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59))
    txns = query.order_by(Transaction.date.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Type", "Category", "Description", "Amount", "Notes"])
    for t in txns:
        d = t.date.strftime("%Y-%m-%d") if isinstance(t.date, (date, datetime)) else str(t.date)
        writer.writerow([d, t.trans_type, t.category or "", t.description, t.amount, t.notes or ""])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"}
    )


# ─────────────────────────────────────────
#  Reports
# ─────────────────────────────────────────
# ─────────────────────────────────────────
#  Bank Statement Import
# ─────────────────────────────────────────
@app.route("/import-statement", methods=["GET"])
@login_required
def import_statement():
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    credit_cards = CreditCard.query.filter_by(user_id=uid()).all()
    loans = Loan.query.filter_by(user_id=uid(), loan_status="Active").all()
    return render_template("import_statement.html", wallets=wallets, credit_cards=credit_cards, loans=loans)


@app.route("/import-statement/preview", methods=["POST"])
@login_required
def import_statement_preview():
    """Parse uploaded CSV and return a preview of detected transactions."""
    file = request.files.get("statement_file")
    if not file or not file.filename:
        flash("Please choose a CSV file to upload.", "danger")
        return redirect(url_for("import_statement"))

    if not file.filename.lower().endswith(".csv"):
        flash("Only CSV files are supported right now. Export your statement as CSV from your bank's online portal.", "danger")
        return redirect(url_for("import_statement"))

    try:
        raw = file.read().decode("utf-8-sig", errors="replace")
    except Exception:
        flash("Could not read the file. Please make sure it's a valid CSV.", "danger")
        return redirect(url_for("import_statement"))

    rows = list(csv.reader(io.StringIO(raw)))
    if len(rows) < 2:
        flash("That file looks empty or has no data rows.", "danger")
        return redirect(url_for("import_statement"))

    # Some bank exports prefix the file with account/address metadata
    # before the real header row (e.g. account number, statement period,
    # "Transaction History" title). Scan the first 15 rows to find the
    # row that actually looks like a header, instead of assuming row 0.
    def row_looks_like_header(r):
        joined = ",".join(r).strip().lower()
        return "date" in joined and "description" in joined and (
            "debit" in joined or "credit" in joined or "amount" in joined
        )

    header_row_idx = None
    for i, r in enumerate(rows[:15]):
        if row_looks_like_header(r):
            header_row_idx = i
            break

    if header_row_idx is None:
        # Fall back to the original assumption if nothing matched
        header_row_idx = 0

    header = [h.strip().lower() for h in rows[header_row_idx]]
    data_rows = rows[header_row_idx + 1:]

    # Auto-detect columns by common header names across Sri Lankan / international bank exports
    def find_col(*candidates):
        for cand in candidates:
            for i, h in enumerate(header):
                if cand in h:
                    return i
        return None

    col_date = find_col("date", "txn date", "transaction date", "value date")
    col_desc = find_col("description", "narration", "particulars", "details", "remarks")
    col_debit = find_col("debit", "withdrawal", "dr")
    col_credit = find_col("credit", "deposit", "cr")
    col_amount = find_col("amount") if col_debit is None and col_credit is None else None

    if col_date is None or col_desc is None or (col_debit is None and col_credit is None and col_amount is None):
        flash(
            f"Could not detect the right columns. Found headers: {', '.join(header)}. "
            "Make sure your CSV has Date, Description, and Debit/Credit (or Amount) columns.",
            "danger"
        )
        return redirect(url_for("import_statement"))

    DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d"]

    def parse_date_str(s):
        s = (s or "").strip()
        if not s:
            return None

        # Some bank exports truncate the year (e.g. "25/05/202" instead of
        # "25/05/2026") when the statement is exported mid-page. If we see
        # a 3-digit year fragment, try to complete it to a plausible 4-digit year.
        parts = re.split(r"[/\-.]", s)
        if len(parts) == 3 and len(parts[2]) == 3 and parts[2].isdigit():
            base = parts[2]
            today = date.today()
            candidates = []
            for last_digit in range(10):
                y = int(base + str(last_digit))
                if 2015 <= y <= today.year:
                    candidates.append(y)
            if candidates:
                # Prefer the most recent plausible year (closest to, but not after, today)
                best_year = max(candidates)
                sep = "/" if "/" in s else ("-" if "-" in s else ".")
                parts[2] = str(best_year)
                s = sep.join(parts)

        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def parse_amount_str(s):
        s = (s or "").replace(",", "").replace("Rs.", "").replace("LKR", "").strip()
        if not s or s in ("-", "\u2014"):
            return 0.0
        try:
            return abs(float(s))
        except ValueError:
            return 0.0

    def looks_like_header_row(row):
        """Detect a repeated header row appearing mid-file (common in
        multi-page bank statement exports)."""
        joined = ",".join(row).strip().lower()
        return "date" in joined and "description" in joined and ("debit" in joined or "credit" in joined)

    def looks_like_metadata_row(row, date_col_idx):
        """Detect address/account-info junk rows that banks sometimes
        insert between statement pages (name, address, 'Page X of Y', etc)."""
        if date_col_idx is None or date_col_idx >= len(row):
            return True
        date_field = (row[date_col_idx] or "").strip()
        if not date_field:
            return True
        # A genuine transaction row's date field should contain digits;
        # junk rows have plain text like "H DILRUKSHA WANNIARA" or labels
        return not any(ch.isdigit() for ch in date_field)

    preview_rows = []
    skipped = 0
    max_col = max(filter(lambda x: x is not None, [col_date, col_desc, col_debit, col_credit, col_amount]))

    for idx, row in enumerate(data_rows):
        if not row or len(row) <= max_col:
            skipped += 1
            continue

        # Skip repeated header rows and address/metadata junk rows that
        # multi-page bank statement CSVs often insert mid-file
        if looks_like_header_row(row) or looks_like_metadata_row(row, col_date):
            skipped += 1
            continue

        d = parse_date_str(row[col_date]) if col_date < len(row) else None
        desc = row[col_desc].strip() if col_desc < len(row) else ""

        if col_amount is not None:
            raw_amt = row[col_amount].replace(",", "").strip()
            amt = parse_amount_str(row[col_amount])
            trans_type = "expense" if raw_amt.startswith("-") else "income"
        else:
            debit_val = parse_amount_str(row[col_debit]) if col_debit is not None and col_debit < len(row) else 0
            credit_val = parse_amount_str(row[col_credit]) if col_credit is not None and col_credit < len(row) else 0
            if debit_val > 0:
                amt, trans_type = debit_val, "expense"
            elif credit_val > 0:
                amt, trans_type = credit_val, "income"
            else:
                skipped += 1
                continue

        if not d or amt <= 0 or not desc:
            skipped += 1
            continue

        desc_lower = desc.lower()
        category = "Other"
        # Sri Lankan bank statement jargon codes (POS, ATM, EFT, CC payments etc.)
        if any(k in desc_lower for k in ["atm wd", "atm fscash", "cash withdrawal"]):
            category = "Cash Withdrawal"
        elif any(k in desc_lower for k in ["cc pmt", "credit card pmt", "card pmt"]):
            category = "Credit Card Payment"
        elif any(k in desc_lower for k in ["service charge", "service fee", "ledger fee"]):
            category = "Bank Fees"
        elif any(k in desc_lower for k in ["witholding tax", "withholding tax", "wht"]):
            category = "Tax"
        elif any(k in desc_lower for k in ["interest account", "interest credit", "int. pay"]):
            category = "Interest Income"
        elif any(k in desc_lower for k in ["eft", "slips", "trf", "transfer", "remit"]):
            category = "Transfer"
        elif any(k in desc_lower for k in ["pos npb", "pos pb", "pos visa", "pos mastercard"]):
            category = "Card Purchase"
        elif any(k in desc_lower for k in ["bill pmt", "bill payment", "billpay"]):
            category = "Bills"
        elif any(k in desc_lower for k in ["just pay", "justpay"]):
            category = "Bills"
        elif any(k in desc_lower for k in ["salary", "payroll"]):
            category = "Salary"
        elif any(k in desc_lower for k in ["grocery", "supermarket", "keells", "cargills", "arpico"]):
            category = "Groceries"
        elif any(k in desc_lower for k in ["fuel", "petrol", "diesel", "ioc", "lanka filling"]):
            category = "Transport"
        elif any(k in desc_lower for k in ["restaurant", "cafe", "food", "kfc", "pizza"]):
            category = "Dining"
        elif any(k in desc_lower for k in ["electricity", "ceb", "water", "nwsdb", "internet", "dialog", "slt", "mobitel"]):
            category = "Utilities"

        preview_rows.append({
            "row_index": idx,
            "date": d.strftime("%Y-%m-%d"),
            "description": desc,
            "amount": amt,
            "trans_type": trans_type,
            "category": category,
        })

    if not preview_rows:
        flash("No valid transactions could be parsed from this file.", "danger")
        return redirect(url_for("import_statement"))

    session["import_preview"] = preview_rows
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    credit_cards = CreditCard.query.filter_by(user_id=uid()).all()
    loans = Loan.query.filter_by(user_id=uid(), loan_status="Active").all()

    existing = Transaction.query.filter_by(user_id=uid()).all()
    existing_keys = {
        (t.date.strftime("%Y-%m-%d") if hasattr(t.date, "strftime") else str(t.date), round(t.amount, 2), (t.description or "").strip().lower())
        for t in existing
    }
    for r in preview_rows:
        key = (r["date"], round(r["amount"], 2), r["description"].strip().lower())
        r["is_duplicate"] = key in existing_keys

        # Suggest a destination per row based on the auto-guessed category.
        # Only auto-suggest credit card / loan links when the user has
        # exactly one of that type — otherwise default to "wallet" and
        # let them pick manually (ambiguous when there are multiple cards).
        if r["category"] == "Credit Card Payment" and len(credit_cards) == 1:
            r["suggested_destination"] = f"cc_payment_{credit_cards[0].id}"
        elif r["category"] == "Bank Fees" or r["category"] == "Tax" or r["category"] == "Interest Income":
            r["suggested_destination"] = "none"
        else:
            r["suggested_destination"] = "wallet"

    dup_count = sum(1 for r in preview_rows if r["is_duplicate"])

    return render_template(
        "import_statement.html",
        wallets=wallets,
        credit_cards=credit_cards,
        loans=loans,
        preview_rows=preview_rows,
        skipped_count=skipped,
        dup_count=dup_count,
        show_preview=True,
    )


@app.route("/import-statement/confirm", methods=["POST"])
@login_required
def import_statement_confirm():
    """Commit the previewed rows as real transactions, respecting each
    row's chosen destination (wallet / credit card purchase / credit card
    payment / loan / record-only)."""
    preview_rows = session.get("import_preview")
    if not preview_rows:
        flash("Your import session expired. Please upload the file again.", "danger")
        return redirect(url_for("import_statement"))

    skip_duplicates = request.form.get("skip_duplicates") == "on"
    default_wallet_id = request.form.get("wallet_id")
    default_wallet_id = int(default_wallet_id) if default_wallet_id else None

    selected_indices = {
        int(k.split("_")[1]) for k in request.form.keys()
        if k.startswith("row_")
    }

    existing = Transaction.query.filter_by(user_id=uid()).all()
    existing_keys = {
        (t.date.strftime("%Y-%m-%d") if hasattr(t.date, "strftime") else str(t.date), round(t.amount, 2), (t.description or "").strip().lower())
        for t in existing
    }

    imported = 0
    skipped_dupes = 0
    cc_payments_applied = 0

    for row in preview_rows:
        if row["row_index"] not in selected_indices:
            continue

        key = (row["date"], round(row["amount"], 2), row["description"].strip().lower())
        if skip_duplicates and key in existing_keys:
            skipped_dupes += 1
            continue

        date_obj = datetime.strptime(row["date"], "%Y-%m-%d").date()

        # Per-row destination: "wallet", "none", "cc_purchase_<id>", "cc_payment_<id>", "loan_<id>"
        destination = request.form.get(f"dest_{row['row_index']}", "wallet")

        wallet_id = None
        linked_credit = None
        linked_loan = None
        card_for_payment = None

        if destination == "wallet":
            wallet_id = default_wallet_id
        elif destination == "none":
            pass
        elif destination.startswith("cc_purchase_"):
            linked_credit = int(destination.split("_")[-1])
        elif destination.startswith("cc_payment_"):
            card_id = int(destination.split("_")[-1])
            wallet_id = default_wallet_id  # the payment still leaves your bank account
            card_for_payment = CreditCard.query.filter_by(id=card_id, user_id=uid()).first()
        elif destination.startswith("loan_"):
            linked_loan = int(destination.split("_")[-1])

        add_transaction(
            amount=row["amount"],
            description=row["description"],
            trans_type=row["trans_type"],
            wallet_id=wallet_id,
            linked_credit=linked_credit,
            linked_loan=linked_loan,
            date_obj=date_obj,
            category=row["category"],
            notes="Imported from bank statement",
            user_id=uid(),
        )

        # Credit card PAYMENT (not purchase) restores available balance,
        # same as the dedicated pay_credit_card flow does.
        if card_for_payment and row["trans_type"] == "expense":
            card_for_payment.available_balance = min(
                card_for_payment.credit_limit,
                card_for_payment.available_balance + row["amount"]
            )
            cc_payments_applied += 1

        imported += 1

    if cc_payments_applied:
        db.session.commit()
        update_networth_snapshot(user_id=uid())

    session.pop("import_preview", None)

    msg = f"\u2705 Imported {imported} transaction{'s' if imported != 1 else ''}."
    if cc_payments_applied:
        msg += f" Updated {cc_payments_applied} credit card balance{'s' if cc_payments_applied != 1 else ''}."
    if skipped_dupes:
        msg += f" Skipped {skipped_dupes} duplicate{'s' if skipped_dupes != 1 else ''}."
    flash(msg, "success")
    return redirect(url_for("transactions"))

@app.route("/reports")
@login_required
def reports():
    today = date.today()
    # Build list of last 12 months for selection
    months = []
    for i in range(12):
        m = (today - relativedelta(months=i)).replace(day=1)
        months.append(m)
    return render_template("reports.html", months=months, today=today)


@app.route("/reports/download")
@login_required
def download_report():
    month_str = request.args.get("month", date.today().strftime("%Y-%m"))
    try:
        month_date = datetime.strptime(month_str, "%Y-%m").date()
    except:
        month_date = date.today().replace(day=1)

    month_start = month_date.replace(day=1)
    month_end = month_start + relativedelta(months=1) - timedelta(days=1)

    # Gather all data for this user
    transactions = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.date >= month_start,
        Transaction.date <= month_end
    ).order_by(Transaction.date.desc()).all()

    wallets  = Wallet.query.filter_by(user_id=uid()).all()
    loans    = Loan.query.filter_by(user_id=uid(), loan_status='Active').all()
    cards    = CreditCard.query.filter_by(user_id=uid()).all()
    goals    = Goal.query.filter_by(user_id=uid()).all()
    fixed    = FixedExpense.query.filter_by(user_id=uid()).all()
    health   = get_financial_health_score(user_id=uid())
    settings = {}

    user     = session.get('user', {})
    currency = get_setting("currency_symbol", "LKR", user_id=uid())

    pdf_bytes = generate_monthly_report(
        user_id      = uid(),
        user_name    = user.get('name', 'User'),
        user_email   = user.get('email', ''),
        currency_symbol = currency,
        month_date   = month_date,
        transactions = transactions,
        wallets      = wallets,
        loans        = loans,
        cards        = cards,
        goals        = goals,
        fixed_expenses = fixed,
        health       = health,
        settings     = settings,
    )

    filename = f"FinanceOS_{month_date.strftime('%B_%Y')}_Report.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ─────────────────────────────────────────
#  Financial Tools
# ─────────────────────────────────────────
@app.route("/tools")
def financial_tools():
    if uid():
        wallets = Wallet.query.filter_by(user_id=uid()).all()
        cards = CreditCard.query.filter_by(user_id=uid()).all()
        loans = Loan.query.filter_by(user_id=uid(), loan_status='Active').all()
        total_monthly_commitments = (
            sum(l.monthly_payment for l in loans) +
            sum(c.minimum_payment for c in cards)
        )
        recent_income = Transaction.query.filter(
            Transaction.user_id == uid(),
            Transaction.trans_type == 'income',
            Transaction.date >= (date.today() - relativedelta(months=3))
        ).all()
        monthly_income_avg = sum(t.amount for t in recent_income) / 3 if recent_income else 0.0
        total_savings = sum(w.balance for w in wallets)
    else:
        wallets, cards, loans = [], [], []
        total_monthly_commitments = 0.0
        monthly_income_avg = 0.0
        total_savings = 0.0
    return render_template("financial_tools.html",
        wallets=wallets, cards=cards, loans=loans,
        total_monthly_commitments=total_monthly_commitments,
        monthly_income_avg=monthly_income_avg,
        total_savings=total_savings,
        is_logged_in=uid() is not None,
    )



@app.route("/api/health-score")
def api_health_score():
    return jsonify(get_financial_health_score())


@app.route("/api/net-worth-history")
def api_net_worth_history():
    history = NetWorthHistory.query.filter_by(user_id=uid()).order_by(NetWorthHistory.date).all()
    return jsonify([{
        "date": h.date.strftime("%Y-%m-%d"),
        "assets": h.total_assets,
        "liabilities": h.total_liabilities,
        "net_worth": h.net_worth
    } for h in history])


@app.route("/api/spending-by-category")
def api_spending_by_category():
    today = date.today()
    start = today.replace(day=1)
    txns = Transaction.query.filter(
        Transaction.trans_type == "expense",
        Transaction.date >= start
    ).all()
    by_cat = defaultdict(float)
    for t in txns:
        by_cat[t.category or "Other"] += t.amount
    return jsonify(dict(by_cat))


import models


# ─────────────────────────────────────────
#  Investments
# ─────────────────────────────────────────
@app.route("/investments")
@login_required
def investments():
    investments = Investment.query.filter_by(user_id=uid()).order_by(Investment.asset_type, Investment.name).all()
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    income_records = InvestmentIncome.query.filter_by(user_id=uid()).order_by(InvestmentIncome.date.desc()).limit(20).all()

    # Group by type
    by_type = {}
    total_invested = 0
    total_value = 0
    for inv in investments:
        if inv.status == 'active':
            t = inv.asset_type
            if t not in by_type:
                by_type[t] = {'items': [], 'invested': 0, 'value': 0}
            by_type[t]['items'].append(inv)
            by_type[t]['invested'] += inv.total_invested
            by_type[t]['value'] += inv.current_value
            total_invested += inv.total_invested
            total_value += inv.current_value

    total_gain = total_value - total_invested
    total_gain_pct = (total_gain / total_invested * 100) if total_invested > 0 else 0

    return render_template('investments.html',
        investments=investments,
        by_type=by_type,
        wallets=wallets,
        income_records=income_records,
        total_invested=total_invested,
        total_value=total_value,
        total_gain=total_gain,
        total_gain_pct=total_gain_pct,
        today=date.today(),
    )


@app.route("/investments/add", methods=["POST"])
@login_required
def add_investment():
    raw_name = request.form.get('name', '').strip()
    if not raw_name:
        institution = request.form.get('institution', '').strip()
        asset_type = request.form.get('asset_type', 'Other')
        raw_name = f"{institution} {asset_type}".strip() if institution else asset_type
    name, err = validate_text(raw_name, "Investment name")
    if err: flash(err, "danger"); return redirect(url_for('investments'))

    units = float(request.form.get('units') or 0)
    purchase_price = float(request.form.get('purchase_price') or 0)
    current_price = float(request.form.get('current_price') or purchase_price)

    inv = Investment(
        user_id=uid(),
        name=name,
        symbol=request.form.get('symbol', '').upper().strip(),
        asset_type=request.form.get('asset_type', 'Other'),
        institution=request.form.get('institution', ''),
        units=units,
        purchase_price=purchase_price,
        current_price=current_price,
        purchase_date=datetime.strptime(request.form['purchase_date'], '%Y-%m-%d').date() if request.form.get('purchase_date') else None,
        maturity_date=datetime.strptime(request.form['maturity_date'], '%Y-%m-%d').date() if request.form.get('maturity_date') else None,
        interest_rate=float(request.form.get('interest_rate') or 0),
        currency=request.form.get('currency', 'LKR'),
        notes=request.form.get('notes', ''),
        wallet_id=int(request.form['wallet_id']) if request.form.get('wallet_id') else None,
    )

    # Deduct from wallet if linked (money went into investment)
    if inv.wallet_id and inv.total_invested > 0:
        wallet = Wallet.query.filter_by(id=inv.wallet_id, user_id=uid()).first()
        if wallet:
            wallet.balance -= inv.total_invested

    db.session.add(inv)
    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash(f"✅ {name} added to investments.", "success")
    return redirect(url_for('investments'))


@app.route("/investments/update-price/<int:inv_id>", methods=["POST"])
@login_required
def update_investment_price(inv_id):
    inv = Investment.query.filter_by(id=inv_id, user_id=uid()).first_or_404()
    new_price = float(request.form.get('current_price') or inv.current_price)
    inv.current_price = new_price
    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash(f"Price updated for {inv.name}.", "success")
    return redirect(url_for('investments'))


@app.route("/investments/income/<int:inv_id>", methods=["POST"])
@login_required
def record_investment_income(inv_id):
    inv = Investment.query.filter_by(id=inv_id, user_id=uid()).first_or_404()
    amount, err = validate_amount(request.form.get('amount'), "Amount")
    if err: flash(err, "danger"); return redirect(url_for('investments'))

    action = request.form.get('action', 'record_only')
    wallet_id = int(request.form['wallet_id']) if request.form.get('wallet_id') else None

    income = InvestmentIncome(
        user_id=uid(),
        investment_id=inv_id,
        income_type=request.form.get('income_type', 'Dividend'),
        amount=amount,
        date=date.today(),
        action=action,
        wallet_id=wallet_id,
        notes=request.form.get('notes', ''),
    )
    db.session.add(income)

    if action == 'add_to_wallet' and wallet_id:
        wallet = Wallet.query.filter_by(id=wallet_id, user_id=uid()).first()
        if wallet:
            wallet.balance += amount
        add_transaction(
            amount=amount, description=f"{inv.name} — {income.income_type}",
            trans_type='income', wallet_id=wallet_id,
            date_obj=date.today(), category='Investment Income', user_id=uid()
        )
    elif action == 'reinvest':
        # Add to units at current price
        if inv.current_price > 0:
            inv.units += amount / inv.current_price

    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash(f"✅ {income.income_type} of {amount:,.0f} recorded.", "success")
    return redirect(url_for('investments'))


@app.route("/investments/sell/<int:inv_id>", methods=["POST"])
@login_required
def sell_investment(inv_id):
    inv = Investment.query.filter_by(id=inv_id, user_id=uid()).first_or_404()
    units_sold = float(request.form.get('units_sold') or inv.units)
    sale_price = float(request.form.get('sale_price') or inv.current_price)
    wallet_id = int(request.form['wallet_id']) if request.form.get('wallet_id') else None

    proceeds = units_sold * sale_price
    gain = (sale_price - inv.purchase_price) * units_sold

    if wallet_id:
        wallet = Wallet.query.filter_by(id=wallet_id, user_id=uid()).first()
        if wallet:
            wallet.balance += proceeds
        add_transaction(
            amount=proceeds, description=f"Sold: {inv.name}",
            trans_type='income', wallet_id=wallet_id,
            date_obj=date.today(), category='Investment Sale', user_id=uid()
        )

    if units_sold >= inv.units:
        inv.status = 'sold'
        inv.units = 0
    else:
        inv.units -= units_sold

    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash(f"✅ Sold {units_sold} units of {inv.name} for {proceeds:,.0f}. {'Gain' if gain >= 0 else 'Loss'}: {abs(gain):,.0f}", "success")
    return redirect(url_for('investments'))


@app.route("/investments/delete/<int:inv_id>", methods=["POST"])
@login_required
def delete_investment(inv_id):
    inv = Investment.query.filter_by(id=inv_id, user_id=uid()).first_or_404()
    db.session.delete(inv)
    db.session.commit()
    flash("Investment deleted.", "success")
    return redirect(url_for('investments'))


# ─────────────────────────────────────────
#  Debt Tracker
# ─────────────────────────────────────────
@app.route("/debts")
@login_required
def debts():
    all_debts = Debt.query.filter_by(user_id=uid()).order_by(Debt.status, Debt.due_date).all()
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    pending = [d for d in all_debts if d.status == 'pending']
    settled = [d for d in all_debts if d.status == 'settled']
    i_owe = sum(d.amount for d in pending if d.direction == 'owe')
    owed_to_me = sum(d.amount for d in pending if d.direction == 'lent')
    return render_template('debts.html',
        debts=all_debts, wallets=wallets,
        pending=pending, settled=settled,
        i_owe=i_owe, owed_to_me=owed_to_me,
        today=date.today(),
    )


@app.route("/debts/add", methods=["POST"])
@login_required
def add_debt():
    contact, err = validate_text(request.form.get('contact_name'), "Contact name")
    if err: flash(err, "danger"); return redirect(url_for('debts'))
    amount, err = validate_amount(request.form.get('amount'), "Amount")
    if err: flash(err, "danger"); return redirect(url_for('debts'))

    direction = request.form.get('direction', 'owe')
    wallet_id = int(request.form['wallet_id']) if request.form.get('wallet_id') else None

    debt = Debt(
        user_id=uid(),
        contact_name=contact,
        amount=amount,
        direction=direction,
        description=request.form.get('description', ''),
        due_date=datetime.strptime(request.form['due_date'], '%Y-%m-%d').date() if request.form.get('due_date') else None,
        wallet_id=wallet_id,
        notes=request.form.get('notes', ''),
    )

    # Record transaction
    if wallet_id:
        wallet = Wallet.query.filter_by(id=wallet_id, user_id=uid()).first()
        if wallet:
            if direction == 'lent':  # I lent money → deduct from wallet
                wallet.balance -= amount
            else:  # I borrowed → add to wallet
                wallet.balance += amount

    db.session.add(debt)
    db.session.commit()
    label = "lent to" if direction == "lent" else "borrowed from"
    flash(f"✅ {get_setting('currency_symbol','LKR',user_id=uid())} {amount:,.0f} {label} {contact} recorded.", "success")
    return redirect(url_for('debts'))


@app.route("/debts/settle/<int:debt_id>", methods=["POST"])
@login_required
def settle_debt(debt_id):
    debt = Debt.query.filter_by(id=debt_id, user_id=uid()).first_or_404()
    wallet_id = int(request.form['wallet_id']) if request.form.get('wallet_id') else None

    if wallet_id:
        wallet = Wallet.query.filter_by(id=wallet_id, user_id=uid()).first()
        if wallet:
            if debt.direction == 'owe':  # I repay → deduct
                wallet.balance -= debt.amount
            else:  # They repay me → add
                wallet.balance += debt.amount

    debt.status = 'settled'
    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash(f"✅ Debt with {debt.contact_name} settled.", "success")
    return redirect(url_for('debts'))


@app.route("/debts/delete/<int:debt_id>", methods=["POST"])
@login_required
def delete_debt(debt_id):
    debt = Debt.query.filter_by(id=debt_id, user_id=uid()).first_or_404()
    db.session.delete(debt)
    db.session.commit()
    flash("Debt record deleted.", "success")
    return redirect(url_for('debts'))


# ─────────────────────────────────────────
#  Exchange Rates
# ─────────────────────────────────────────
@app.route("/exchange-rates")
def exchange_rates():
    import requests as http

    # Fallback rates (approximate — used if API is down)
    FALLBACK_RATES = {
        "USD": {"flag": "🇺🇸", "name": "US Dollar",          "lkr_per_unit": 315.00},
        "EUR": {"flag": "🇪🇺", "name": "Euro",               "lkr_per_unit": 341.00},
        "GBP": {"flag": "🇬🇧", "name": "British Pound",      "lkr_per_unit": 399.00},
        "AUD": {"flag": "🇦🇺", "name": "Australian Dollar",  "lkr_per_unit": 202.00},
        "SGD": {"flag": "🇸🇬", "name": "Singapore Dollar",   "lkr_per_unit": 234.00},
        "INR": {"flag": "🇮🇳", "name": "Indian Rupee",       "lkr_per_unit": 3.78},
        "JPY": {"flag": "🇯🇵", "name": "Japanese Yen",       "lkr_per_unit": 2.09},
        "CAD": {"flag": "🇨🇦", "name": "Canadian Dollar",    "lkr_per_unit": 230.00},
        "CNY": {"flag": "🇨🇳", "name": "Chinese Yuan",       "lkr_per_unit": 43.40},
        "AED": {"flag": "🇦🇪", "name": "UAE Dirham",         "lkr_per_unit": 85.75},
        "SAR": {"flag": "🇸🇦", "name": "Saudi Riyal",        "lkr_per_unit": 84.00},
        "MYR": {"flag": "🇲🇾", "name": "Malaysian Ringgit",  "lkr_per_unit": 67.00},
        "THB": {"flag": "🇹🇭", "name": "Thai Baht",          "lkr_per_unit": 8.75},
        "CHF": {"flag": "🇨🇭", "name": "Swiss Franc",        "lkr_per_unit": 354.00},
        "NZD": {"flag": "🇳🇿", "name": "New Zealand Dollar", "lkr_per_unit": 188.00},
    }

    rates = {}
    error = None
    last_updated = None
    using_fallback = False

    CURRENCIES = [
        ("USD", "🇺🇸", "US Dollar"), ("EUR", "🇪🇺", "Euro"),
        ("GBP", "🇬🇧", "British Pound"), ("AUD", "🇦🇺", "Australian Dollar"),
        ("SGD", "🇸🇬", "Singapore Dollar"), ("INR", "🇮🇳", "Indian Rupee"),
        ("JPY", "🇯🇵", "Japanese Yen"), ("CAD", "🇨🇦", "Canadian Dollar"),
        ("CNY", "🇨🇳", "Chinese Yuan"), ("AED", "🇦🇪", "UAE Dirham"),
        ("SAR", "🇸🇦", "Saudi Riyal"), ("MYR", "🇲🇾", "Malaysian Ringgit"),
        ("THB", "🇹🇭", "Thai Baht"), ("CHF", "🇨🇭", "Swiss Franc"),
        ("NZD", "🇳🇿", "New Zealand Dollar"),
    ]

    # Try multiple APIs in order
    api_urls = [
        "https://api.exchangerate-api.com/v4/latest/USD",
        "https://open.er-api.com/v6/latest/USD",
        "https://api.frankfurter.app/latest?from=USD",
    ]

    for api_url in api_urls:
        try:
            resp = http.get(api_url, timeout=8, headers={"User-Agent": "FinanceOS/1.0"})
            data = resp.json()

            # Handle different API response formats
            r = data.get("rates") or data.get("Rates") or {}
            lkr = r.get("LKR") or r.get("lkr") or 315.0
            last_updated = (data.get("time_last_update_utc")
                           or data.get("date")
                           or data.get("updated", ""))

            if lkr and r:
                for code, flag, name in CURRENCIES:
                    rate_val = r.get(code) or r.get(code.lower())
                    if rate_val:
                        rates[code] = {
                            "flag": flag,
                            "name": name,
                            "lkr_per_unit": round(lkr / rate_val, 2),
                        }
                if rates:
                    break  # Success — stop trying other APIs
        except Exception:
            continue

    # Use fallback if all APIs failed
    if not rates:
        rates = FALLBACK_RATES.copy()
        using_fallback = True
        error = "Live rates unavailable — showing approximate rates. Refresh to try again."

    currency_symbol = get_setting("currency_symbol", "LKR", user_id=uid())
    return render_template("exchange_rates.html",
        rates=rates,
        error=error,
        last_updated=last_updated,
        currency_symbol=currency_symbol,
        using_fallback=using_fallback,
    )


# ─────────────────────────────────────────
#  CSE Stock Prices
# ─────────────────────────────────────────
@app.route("/cse")
def cse_stocks():
    import requests as http
    from datetime import datetime as dt
    error = None
    top_gainers = []
    top_losers = []
    most_active = []
    market_open = False

    # Check if market is open (Mon-Fri 9:30-14:30 Colombo = UTC+5:30)
    now_utc = dt.utcnow()
    now_colombo_hour = (now_utc.hour + 5) % 24
    now_colombo_min  = (now_utc.minute + 30) % 60
    now_colombo_time = now_colombo_hour * 60 + now_colombo_min
    is_weekday = now_utc.weekday() < 5
    market_open = is_weekday and (9*60+30) <= now_colombo_time <= (14*60+30)

    # Try multiple header combinations — CSE API is strict
    header_sets = [
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.cse.lk",
            "Referer": "https://www.cse.lk/trade-summary",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        },
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": "https://www.cse.lk",
            "Referer": "https://www.cse.lk/",
        },
    ]

    def cse_post(session, endpoint, response_key):
        for headers in header_sets:
            try:
                r = session.post(
                    f"https://www.cse.lk/api/{endpoint}",
                    headers=headers,
                    data={},
                    timeout=12,
                )
                if r.status_code == 200:
                    data = r.json()
                    # API may return plain list OR dict with key
                    if isinstance(data, list) and data:
                        return data
                    elif isinstance(data, dict):
                        # Try the expected key first
                        items = data.get(response_key, [])
                        if items:
                            return items
                        # Try common fallback keys
                        for key in data:
                            val = data[key]
                            if isinstance(val, list) and val:
                                return val
            except Exception:
                continue
        return []

    session = http.Session()
    # Seed cookies
    try:
        session.get("https://www.cse.lk/", timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    except Exception:
        pass

    try:
        top_gainers = cse_post(session, "topGainers", "reqTopGainers")[:10]
        top_losers  = cse_post(session, "topLooses",  "reqTopLooses")[:10]
        most_active = cse_post(session, "mostActiveTrades", "reqMostActiveTrades")[:10]
        if not top_gainers and not top_losers and not most_active:
            error = "market_closed" if not market_open else "api_down"
    except Exception as e:
        error = "api_down"

    favourites = FavouriteStock.query.filter_by(user_id=uid()).order_by(FavouriteStock.added_at).all() if uid() else []
    return render_template("cse_stocks.html",
        top_gainers=top_gainers,
        top_losers=top_losers,
        most_active=most_active,
        favourites=favourites,
        error=error,
        market_open=market_open,
        is_logged_in=uid() is not None,
    )


@app.route("/cse/search", methods=["POST"])
@login_required
def cse_search():
    import requests as http
    symbol = request.form.get("symbol", "").strip().upper()
    if not symbol:
        return jsonify({"error": "Symbol required"})

    # Try with and without suffix
    symbols_to_try = []
    if "." not in symbol:
        symbols_to_try = [symbol + ".N0000", symbol + ".X0000", symbol + ".B0000"]
    else:
        symbols_to_try = [symbol]

    header_sets = [
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.cse.lk",
            "Referer": "https://www.cse.lk/trade-summary",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        },
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.cse.lk",
            "Referer": "https://www.cse.lk/",
        },
    ]

    session = http.Session()
    try:
        session.get("https://www.cse.lk/", timeout=6,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    except Exception:
        pass

    for sym in symbols_to_try:
        for headers in header_sets:
            try:
                r = session.post(
                    "https://www.cse.lk/api/companyInfoSummery",
                    data={"symbol": sym},
                    headers=headers,
                    timeout=12,
                )
                if r.status_code == 200:
                    data = r.json()
                    info = data.get("reqSymbolInfo", {})
                    if info:
                        # Normalise field names for template
                        return jsonify({
                            "symbol": info.get("symbol", sym),
                            "name": info.get("name", info.get("companyName", "")),
                            "lastTradedPrice": info.get("lastTradedPrice", info.get("closingPrice", 0)),
                            "closingPrice": info.get("closingPrice", 0),
                            "changePercentage": info.get("changePercentage", info.get("changePct", 0)),
                            "change": info.get("change", 0),
                            "volume": info.get("volume", info.get("totalVolume", 0)),
                            "marketCap": info.get("marketCap", 0),
                            "52WeekHigh": info.get("52WeekHigh", info.get("yearHigh", 0)),
                            "52WeekLow": info.get("52WeekLow", info.get("yearLow", 0)),
                        })
            except Exception:
                continue

    return jsonify({"error": "not_found"})


# ─────────────────────────────────────────
#  CSE Favourites
# ─────────────────────────────────────────
@app.route("/cse/favourites/add", methods=["POST"])
@login_required
def add_favourite_stock():
    symbol = request.form.get("symbol", "").strip().upper()
    name = request.form.get("name", "").strip()
    if not symbol:
        return jsonify({"error": "Symbol required"})
    # Add .N0000 if no suffix
    if "." not in symbol:
        symbol = symbol + ".N0000"
    # Check duplicate
    existing = FavouriteStock.query.filter_by(user_id=uid(), symbol=symbol).first()
    if existing:
        return jsonify({"error": "Already in favourites"})
    fav = FavouriteStock(user_id=uid(), symbol=symbol, display_name=name)
    db.session.add(fav)
    db.session.commit()
    return jsonify({"success": True, "id": fav.id, "symbol": symbol, "name": name})


@app.route("/cse/favourites/remove", methods=["POST"])
@login_required
def remove_favourite_stock():
    symbol = request.form.get("symbol", "").strip().upper()
    if "." not in symbol:
        symbol = symbol + ".N0000"
    fav = FavouriteStock.query.filter_by(user_id=uid(), symbol=symbol).first()
    if fav:
        db.session.delete(fav)
        db.session.commit()
    return jsonify({"success": True})


@app.route("/cse/favourites/prices", methods=["POST"])
@login_required
def favourite_stock_prices():
    """Fetch live prices for all favourite stocks — called by JS polling."""
    import requests as http
    favs = FavouriteStock.query.filter_by(user_id=uid()).all()
    if not favs:
        return jsonify({})

    header_sets = [
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.cse.lk",
            "Referer": "https://www.cse.lk/trade-summary",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        },
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.cse.lk",
            "Referer": "https://www.cse.lk/",
        },
    ]

    session = http.Session()
    try:
        session.get("https://www.cse.lk/", timeout=6,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    except Exception:
        pass

    results = {}
    for fav in favs:
        sym = fav.symbol
        if "." not in sym:
            sym = sym + ".N0000"
        fetched = False
        for headers in header_sets:
            try:
                r = session.post(
                    "https://www.cse.lk/api/companyInfoSummery",
                    data={"symbol": sym},
                    headers=headers,
                    timeout=12,
                )
                if r.status_code == 200:
                    d = r.json().get("reqSymbolInfo", {})
                    if d:
                        results[fav.symbol] = {
                            "price": d.get("price") or d.get("lastTradedPrice") or d.get("closingPrice") or 0,
                            "change": d.get("changePercentage") or d.get("changePct") or 0,
                        }
                        fetched = True
                        break
            except Exception:
                continue
        if not fetched:
            results[fav.symbol] = {"price": None, "change": None}

    return jsonify(results)


# ─────────────────────────────────────────
#  Credit Card Offers
# ─────────────────────────────────────────
@app.route("/offers")
def card_offers():
    banks = Bank.query.filter_by(is_active=True).order_by(Bank.sort_order, Bank.name).all()
    return render_template(
        "card_offers.html",
        banks=banks,
        is_admin=_is_admin(),
    )


def _is_admin():
    """Check if current user is admin (you — Hiroshan)."""
    user = session.get("user", {})
    admin_emails = ["hiroshann@gmail.com"]  # add your email here
    return user.get("email", "") in admin_emails


@app.route("/offers/bank/add", methods=["POST"])
@login_required
def admin_bank_add():
    if not _is_admin():
        flash("Not authorised.", "danger")
        return redirect(url_for("card_offers"))

    name = request.form.get("name", "").strip()
    offers_url = request.form.get("offers_url", "").strip()
    if not name or not offers_url:
        flash("Bank name and offers URL are both required.", "danger")
        return redirect(url_for("card_offers"))

    existing = Bank.query.filter(db.func.lower(Bank.name) == name.lower()).first()
    if existing:
        flash(f"{name} is already in the directory.", "danger")
        return redirect(url_for("card_offers"))

    bank = Bank(
        name=name,
        short_code=(request.form.get("short_code", "").strip() or "".join(w[0] for w in name.split()[:2]).upper()),
        logo_url=request.form.get("logo_url", "").strip() or None,
        offers_url=offers_url,
        color=request.form.get("color", "accent"),
        sort_order=Bank.query.count(),
    )
    db.session.add(bank)
    db.session.commit()
    flash(f"✅ {name} added.", "success")
    return redirect(url_for("card_offers"))


@app.route("/offers/bank/edit/<int:bank_id>", methods=["POST"])
@login_required
def admin_bank_edit(bank_id):
    if not _is_admin():
        flash("Not authorised.", "danger")
        return redirect(url_for("card_offers"))

    bank = Bank.query.get_or_404(bank_id)
    name = request.form.get("name", "").strip()
    offers_url = request.form.get("offers_url", "").strip()
    if not name or not offers_url:
        flash("Bank name and offers URL are both required.", "danger")
        return redirect(url_for("card_offers"))

    bank.name = name
    bank.short_code = request.form.get("short_code", "").strip() or bank.short_code
    bank.logo_url = request.form.get("logo_url", "").strip() or None
    bank.offers_url = offers_url
    bank.color = request.form.get("color", bank.color)
    db.session.commit()
    flash(f"✅ {bank.name} updated.", "success")
    return redirect(url_for("card_offers"))


@app.route("/offers/bank/delete/<int:bank_id>", methods=["POST"])
@login_required
def admin_bank_delete(bank_id):
    if not _is_admin():
        flash("Not authorised.", "danger")
        return redirect(url_for("card_offers"))
    bank = Bank.query.get_or_404(bank_id)
    db.session.delete(bank)
    db.session.commit()
    flash(f"{bank.name} removed.", "success")
    return redirect(url_for("card_offers"))


@app.route("/offers/bank/toggle/<int:bank_id>", methods=["POST"])
@login_required
def admin_bank_toggle(bank_id):
    if not _is_admin():
        flash("Not authorised.", "danger")
        return redirect(url_for("card_offers"))
    bank = Bank.query.get_or_404(bank_id)
    bank.is_active = not bank.is_active
    db.session.commit()
    return redirect(url_for("card_offers"))


# ─────────────────────────────────────────
#  AI Financial Advisor
# ─────────────────────────────────────────
def _build_financial_context(user_id):
    """Build a rich financial context string for the AI."""
    try:
        today = date.today()
        period_start, period_end = get_salary_period(today)
        currency = get_setting("currency_symbol", "LKR", user_id=user_id)
        preferred_name = get_setting("preferred_name", "User", user_id=user_id)

        # Wallets
        wallets = Wallet.query.filter_by(user_id=user_id).all()
        total_wallet = sum(w.balance for w in wallets)

        # Transactions this period
        txns = Transaction.query.filter(
            Transaction.user_id == user_id,
            Transaction.date >= period_start,
            Transaction.date <= period_end
        ).all()
        total_income = sum(t.amount for t in txns if t.trans_type == "income")
        total_expense = sum(t.amount for t in txns if t.trans_type == "expense")

        # Top spending categories
        from collections import defaultdict
        cat_totals = defaultdict(float)
        for t in txns:
            if t.trans_type == "expense":
                cat_totals[t.category or "Other"] += t.amount
        top_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)[:5]

        # Credit cards
        cards = CreditCard.query.filter_by(user_id=user_id).all()
        total_credit_limit = sum(c.credit_limit for c in cards)
        total_credit_used = sum(c.credit_limit - c.available_balance for c in cards)

        # Loans
        loans = Loan.query.filter_by(user_id=user_id, loan_status="Active").all()
        total_loan_balance = sum(l.outstanding_balance for l in loans)

        # Goals
        goals = Goal.query.filter_by(user_id=user_id, status="active").all()

        # Investments
        investments = Investment.query.filter_by(user_id=user_id, status="active").all()
        total_investments = sum(inv.current_value for inv in investments)

        # Savings rate
        savings_rate = round((total_income - total_expense) / total_income * 100, 1) if total_income > 0 else 0

        # Net worth
        net_worth = total_wallet + total_investments - total_loan_balance - total_credit_used

        # Upcoming payments
        upcoming = FixedExpense.query.filter_by(user_id=user_id).filter(
            FixedExpense.date >= today,
            FixedExpense.date <= period_end
        ).all()
        unpaid_total = sum(e.amount for e in upcoming)

        ctx = f"""You are the FinanceOS AI Advisor for {preferred_name}. You have access to their real financial data below. Give specific, actionable, personalized advice. Be concise and friendly. Use {currency} for amounts. Never make up numbers — only use what's provided.

CURRENT FINANCIAL SNAPSHOT ({today.strftime('%d %b %Y')}):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PERIOD: {period_start.strftime('%d %b')} → {period_end.strftime('%d %b %Y')}

INCOME & EXPENSES (this period):
• Total Income: {currency} {total_income:,.0f}
• Total Expenses: {currency} {total_expense:,.0f}
• Net Balance: {currency} {total_income - total_expense:,.0f}
• Savings Rate: {savings_rate}%

WALLETS (actual cash):
"""
        for w in wallets:
            ctx += f"• {w.name} ({w.wallet_type}): {currency} {w.balance:,.0f}\n"
        ctx += f"• TOTAL CASH: {currency} {total_wallet:,.0f}\n"

        ctx += f"""
UPCOMING PAYMENTS (this period):
• Unpaid commitments: {currency} {unpaid_total:,.0f}
• Available after paying fixed: {currency} {total_wallet - unpaid_total:,.0f}

TOP SPENDING CATEGORIES:
"""
        for cat, amt in top_cats:
            ctx += f"• {cat}: {currency} {amt:,.0f}\n"


        if cards:
            ctx += f"\nCREDIT CARDS:\n"
            for c in cards:
                used = c.credit_limit - c.available_balance
                util = round(used / c.credit_limit * 100) if c.credit_limit > 0 else 0
                ctx += f"• {c.bank_name}: {currency} {used:,.0f} used of {currency} {c.credit_limit:,.0f} ({util}% utilization), min payment {currency} {c.minimum_payment:,.0f}, due {c.due_date.strftime('%d %b') if c.due_date else 'N/A'}\n"

        if loans:
            ctx += f"\nACTIVE LOANS:\n"
            for l in loans:
                ctx += f"• {l.loan_name}: {currency} {l.outstanding_balance:,.0f} outstanding, monthly payment {currency} {l.monthly_payment:,.0f}\n"

        if goals:
            ctx += f"\nSAVINGS GOALS:\n"
            for g in goals:
                pct = round(g.current_amount / g.target_amount * 100) if g.target_amount > 0 else 0
                needed = g.target_amount - g.current_amount
                ctx += f"• {g.name}: {currency} {g.current_amount:,.0f} of {currency} {g.target_amount:,.0f} ({pct}%), need {currency} {needed:,.0f} more\n"

        if investments:
            ctx += f"\nINVESTMENTS:\n"
            for inv in investments:
                ctx += f"• {inv.name} ({inv.asset_type}): current value {currency} {inv.current_value:,.0f}, gain/loss {currency} {inv.gain_loss:,.0f} ({inv.gain_loss_pct:+.1f}%)\n"

        ctx += f"""
NET WORTH SUMMARY:
• Cash in wallets: {currency} {total_wallet:,.0f}
• Investments: {currency} {total_investments:,.0f}
• Outstanding loans: {currency} {total_loan_balance:,.0f}
• Credit card debt: {currency} {total_credit_used:,.0f}
• NET WORTH: {currency} {net_worth:,.0f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Answer questions using this data. For questions about future planning, be realistic based on current income/expense patterns. Keep responses concise (3-5 sentences max unless asked for detail). Use bullet points for clarity when listing items."""

        return ctx
    except Exception as e:
        return f"You are a helpful financial advisor. An error occurred loading user data: {str(e)}"


@app.route("/advisor")
@login_required
def advisor_page():
    return render_template("advisor.html",
        preferred_name=get_setting("preferred_name", session.get("user", {}).get("name", "").split()[0] or "there", user_id=uid())
    )


@app.route("/advisor/chat", methods=["POST"])
@login_required
def advisor_chat():
    import logging
    logger = logging.getLogger(__name__)

    data = request.get_json()
    messages = data.get("messages", [])

    if not messages:
        return jsonify({"error": "No messages"}), 400


    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")


    if not anthropic_key:
        return jsonify({"error": "ANTHROPIC_API_KEY missing"}), 500

    system_context = _build_financial_context(uid())

    api_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
    ]

    try:
        resp = http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": anthropic_key,
        },
        json={
            "model": "claude-3-5-sonnet-latest",
            "max_tokens": 1024,
            "system": system_context,
            "messages": api_messages,
        },
        timeout=30,
    )

        if resp.status_code != 200:
            logger.error(f"Anthropic error: {resp.text}")
        return jsonify({
            "error": "AI API error",
            "details": resp.text
        }), 500

        result = resp.json()

        text = ""
        if result.get("content"):
            text = result["content"][0].get("text", "")

        return jsonify({"response": text})

    except Exception as e:
        logger.error(f"Advisor chat error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# ─────────────────────────────────────────
#  Calendar page + Analytics heatmap data
# ─────────────────────────────────────────
@app.route("/calendar")
@login_required
def calendar_view():
    from calendar import monthrange
    today = date.today()
    month_str = request.args.get("month", today.strftime("%Y-%m"))
    try:
        year, month = int(month_str.split("-")[0]), int(month_str.split("-")[1])
    except:
        year, month = today.year, today.month

    month_start = date(year, month, 1)
    _, days_in_month = monthrange(year, month)
    month_end = date(year, month, days_in_month)

    # All transactions for the month
    txns = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.date >= month_start,
        Transaction.date <= month_end
    ).order_by(Transaction.date).all()

    # Group by day — keep SQLAlchemy objects for template, build JSON-safe copy separately
    cal_data = defaultdict(list)
    cal_data_json = {}
    for t in txns:
        d = t.date.day if isinstance(t.date, date) else t.date.date().day
        cal_data[d].append(t)

    # Build JSON-serialisable version for JS
    for day, txs in cal_data.items():
        cal_data_json[day] = [{
            "description": t.description or "",
            "category": t.category or "General",
            "amount": float(t.amount),
            "trans_type": t.trans_type,
        } for t in txs]

    # Daily totals for summary
    daily_totals = {}
    for day, txs in cal_data.items():
        income = sum(t.amount for t in txs if t.trans_type == "income")
        expense = sum(t.amount for t in txs if t.trans_type == "expense")
        daily_totals[day] = {"income": income, "expense": expense, "count": len(txs)}

    # Month summary
    total_income = sum(t.amount for t in txns if t.trans_type == "income")
    total_expense = sum(t.amount for t in txns if t.trans_type == "expense")

    # Prev/next month
    if month == 1:
        prev_month = f"{year-1}-12"
    else:
        prev_month = f"{year}-{month-1:02d}"
    if month == 12:
        next_month = f"{year+1}-01"
    else:
        next_month = f"{year}-{month+1:02d}"

    currency = get_setting("currency_symbol", "LKR", user_id=uid())

    wallets = Wallet.query.filter_by(user_id=uid()).all()
    credit_cards = CreditCard.query.filter_by(user_id=uid()).all()
    expense_categories = get_user_categories(uid(), "expense")

    return render_template("calendar.html",
        month_start=month_start,
        month_end=month_end,
        days_in_month=days_in_month,
        cal_data=cal_data,
        cal_data_json=cal_data_json,
        daily_totals=daily_totals,
        total_income=total_income,
        total_expense=total_expense,
        today=today,
        year=year,
        month=month,
        month_str=month_str,
        prev_month=prev_month,
        next_month=next_month,
        currency_symbol=currency,
        wallets=wallets,
        credit_cards=credit_cards,
        expense_categories=expense_categories,
    )


@app.route("/analytics/heatmap-data")
@login_required
def heatmap_data():
    """Return last 12 months of daily spending for heatmap."""
    today = date.today()
    year_ago = today - timedelta(days=365)

    txns = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.trans_type == "expense",
        Transaction.date >= year_ago,
        Transaction.date <= today
    ).all()

    daily = defaultdict(float)
    for t in txns:
        d = t.date if isinstance(t.date, date) else t.date.date()
        daily[d.strftime("%Y-%m-%d")] += t.amount

    return jsonify(dict(daily))



@app.route("/run-welcome-migration")
def run_welcome_migration():
    """One-time fix — mark all existing users as already welcomed. Remove after running."""
    try:
        # Get all unique user_ids that have any data
        user_ids = set()
        for model in [Transaction, Wallet, CreditCard, Loan, Goal, AppSettings]:
            rows = db.session.query(model.user_id).distinct().all()
            for row in rows:
                if row[0]:
                    user_ids.add(row[0])

        count = 0
        for user_id in user_ids:
            existing = AppSettings.query.filter_by(
                user_id=user_id, key='welcome_sent'
            ).first()
            if not existing:
                db.session.add(AppSettings(
                    user_id=user_id,
                    key='welcome_sent',
                    value='true'
                ))
                count += 1

        db.session.commit()
        return f"✅ Marked {count} existing users as welcomed. Remove this route now."
    except Exception as e:
        return f"Error: {e}"



def send_monthly_summary_email(user_id, user_email, user_name):
    """Send monthly financial summary email."""
    from datetime import date, timedelta
    from collections import defaultdict
    import calendar

    today = date.today()
    # Get the period that just ended
    period_start, period_end = get_salary_period(today - timedelta(days=5))
    currency = get_setting("currency_symbol", "LKR", user_id=user_id)

    # ── Income ──
    income_txns = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.trans_type == "income",
        Transaction.date >= period_start,
        Transaction.date <= period_end,
    ).order_by(Transaction.date).all()
    total_income = sum(t.amount for t in income_txns)

    # ── Expenses by category ──
    expense_txns = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.trans_type == "expense",
        Transaction.date >= period_start,
        Transaction.date <= period_end,
    ).order_by(Transaction.date).all()
    total_expense = sum(t.amount for t in expense_txns)
    cat_totals = defaultdict(lambda: {"amount": 0, "count": 0})
    for t in expense_txns:
        cat = t.category or "Other"
        cat_totals[cat]["amount"] += t.amount
        cat_totals[cat]["count"] += 1
    top_cats = sorted(cat_totals.items(), key=lambda x: x[1]["amount"], reverse=True)[:6]

    saved = total_income - total_expense
    savings_rate = round(saved / total_income * 100, 1) if total_income > 0 else 0

    # ── Next period fixed commitments ──
    next_period_start, next_period_end = get_salary_period(period_end + timedelta(days=2))
    fixed_expenses = FixedExpense.query.filter_by(user_id=user_id).all()
    recurring = RecurringPayment.query.filter_by(user_id=user_id, is_active=True).all()
    cards = CreditCard.query.filter_by(user_id=user_id).all()
    loans = Loan.query.filter_by(user_id=user_id, loan_status="Active").all()

    commitments = []
    for f in fixed_expenses:
        commitments.append({"name": f.name, "amount": f.amount, "due": f.date.strftime("%d %b") if f.date else "—"})
    for r in recurring:
        commitments.append({"name": r.name, "amount": r.amount, "due": f"Monthly"})
    for c in cards:
        if c.minimum_payment:
            commitments.append({"name": f"{c.bank_name} — Min Payment", "amount": c.minimum_payment, "due": c.due_date.strftime("%d %b") if c.due_date else "—"})
    for l in loans:
        commitments.append({"name": f"{l.loan_name} — EMI", "amount": l.monthly_payment, "due": "Monthly"})

    total_committed = sum(c["amount"] for c in commitments)
    available_after = total_income - total_committed

    # ── Credit cards ──
    credit_rows = []
    for c in cards:
        used = c.credit_limit - c.available_balance
        util = round(used / c.credit_limit * 100) if c.credit_limit > 0 else 0
        credit_rows.append({
            "bank": c.bank_name,
            "limit": c.credit_limit,
            "used": used,
            "util": util,
            "due": c.due_date.strftime("%d %b") if c.due_date else "—",
            "warning": util > 60,
        })
    total_credit_used = sum(r["used"] for r in credit_rows)

    # ── Loans ──
    loan_rows = []
    for l in loans:
        loan_rows.append({
            "name": l.loan_name,
            "outstanding": l.outstanding_balance,
            "monthly": l.monthly_payment,
        })
    total_outstanding = sum(r["outstanding"] for r in loan_rows)

    # ── Health score ──
    health = get_financial_health_score(user_id=user_id)

    # ── Build HTML ──
    CAT_COLORS = ['#6366f1','#ef4444','#f59e0b','#10b981','#3b82f6','#8b5cf6']

    def fmt(n): return f"{n:,.0f}"

    income_rows_html = "".join(f"""
    <tr>
      <td style="padding:8px 12px;color:#1a1f37;">{t.description or t.category or 'Income'}</td>
      <td style="padding:8px 12px;color:#64748b;text-align:right;">{t.date.strftime('%d %b') if t.date else '—'}</td>
      <td style="padding:8px 12px;color:#15803d;text-align:right;font-weight:600;">+{fmt(t.amount)}</td>
    </tr>""" for t in income_txns)

    cat_rows_html = "".join(f"""
    <tr>
      <td style="padding:8px 12px;">
        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{CAT_COLORS[i % 6]};margin-right:8px;vertical-align:middle;"></span>
        <span style="color:#1a1f37;">{cat}</span>
      </td>
      <td style="padding:8px 12px;color:#64748b;text-align:right;">{data['count']} txns</td>
      <td style="padding:8px 12px;color:#1a1f37;text-align:right;font-weight:600;">{fmt(data['amount'])}</td>
    </tr>""" for i, (cat, data) in enumerate(top_cats))

    commitment_rows_html = "".join(f"""
    <tr>
      <td style="padding:7px 12px;color:#1a1f37;">{c['name']}</td>
      <td style="padding:7px 12px;color:#64748b;text-align:right;">{c['due']}</td>
      <td style="padding:7px 12px;color:#92400e;text-align:right;font-weight:600;">{fmt(c['amount'])}</td>
    </tr>""" for c in commitments)

    card_rows_html = "".join(f"""
    <tr>
      <td style="padding:7px 12px;color:#1a1f37;font-weight:600;">{r['bank']}</td>
      <td style="padding:7px 12px;color:#64748b;text-align:right;">{fmt(r['limit'])}</td>
      <td style="padding:7px 12px;color:#1a1f37;text-align:right;font-weight:600;">{fmt(r['used'])}</td>
      <td style="padding:7px 12px;text-align:right;">
        <span style="font-size:11px;font-weight:700;color:{'#dc2626' if r['util']>60 else '#15803d'};">{r['util']}%</span>
      </td>
      <td style="padding:7px 12px;color:#64748b;text-align:right;">{r['due']}</td>
    </tr>""" for r in credit_rows)

    card_warnings_html = "".join(f"""
    <div style="font-size:12px;color:#dc2626;margin-top:6px;padding:8px 12px;background:#fef2f2;border-radius:8px;border:1px solid #fecaca;">
      ⚠️ {r['bank']} at {r['util']}% utilization — aim to reduce below 30%
    </div>""" for r in credit_rows if r['warning'])

    loan_rows_html = "".join(f"""
    <tr>
      <td style="padding:7px 12px;color:#1a1f37;font-weight:600;">{r['name']}</td>
      <td style="padding:7px 12px;color:#dc2626;text-align:right;font-weight:600;">{fmt(r['outstanding'])}</td>
      <td style="padding:7px 12px;color:#64748b;text-align:right;">{fmt(r['monthly'])}/mo</td>
    </tr>""" for r in loan_rows) if loan_rows else ""

    savings_bar_w = min(savings_rate, 100)
    score_bar_w = min(health.score, 100)
    score_color = health.color

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
body{{margin:0;padding:0;background:#f0ebe3;font-family:'Segoe UI',Arial,sans-serif;}}
.wrap{{max-width:600px;margin:0 auto;padding:28px 16px;}}
.card{{background:#fff;border-radius:20px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.07);}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th{{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;color:#94a3b8;padding:8px 12px;background:#f8fafc;text-align:left;border-bottom:1px solid #f1f5f9;}}
tr+tr td{{border-top:1px solid #f8fafc;}}
.tfoot td{{border-top:2px solid #f1f5f9 !important;background:#f8fafc;font-weight:700;}}
.section-label{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:#94a3b8;margin-bottom:8px;display:block;}}
</style>
</head>
<body>
<div class="wrap"><div class="card">

<!-- Hero -->
<div style="background:#0f172a;padding:36px;text-align:center;">
  <div style="display:inline-flex;align-items:center;gap:10px;margin-bottom:16px;">
    <div style="width:36px;height:36px;background:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:10px;display:inline-flex;align-items:center;justify-content:center;color:white;font-size:16px;font-weight:800;">F</div>
    <span style="color:white;font-size:16px;font-weight:800;letter-spacing:-0.02em;">FinanceOS</span>
  </div>
  <div style="color:rgba(255,255,255,0.4);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">Monthly Financial Summary</div>
  <div style="color:white;font-size:24px;font-weight:800;letter-spacing:-0.02em;">{period_start.strftime('%B %Y')}</div>
  <div style="color:rgba(255,255,255,0.35);font-size:12px;margin-top:4px;">{period_start.strftime('%d %b')} – {period_end.strftime('%d %b %Y')} · Salary Period</div>
</div>

<!-- Summary bar -->
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;border-bottom:1px solid #f1f5f9;">
  <div style="padding:16px;text-align:center;border-right:1px solid #f1f5f9;">
    <div style="font-size:10px;color:#94a3b8;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:5px;">Income</div>
    <div style="font-size:20px;font-weight:800;color:#15803d;">{fmt(total_income)}</div>
    <div style="font-size:10px;color:#94a3b8;">{currency}</div>
  </div>
  <div style="padding:16px;text-align:center;border-right:1px solid #f1f5f9;">
    <div style="font-size:10px;color:#94a3b8;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:5px;">Expenses</div>
    <div style="font-size:20px;font-weight:800;color:#dc2626;">{fmt(total_expense)}</div>
    <div style="font-size:10px;color:#94a3b8;">{currency}</div>
  </div>
  <div style="padding:16px;text-align:center;">
    <div style="font-size:10px;color:#94a3b8;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:5px;">Saved</div>
    <div style="font-size:20px;font-weight:800;color:#6366f1;">{fmt(saved)}</div>
    <div style="font-size:10px;color:#6366f1;">{savings_rate}% rate</div>
  </div>
</div>

<div style="padding:24px;">

<!-- 1. Income -->
<span class="section-label">↓ Income this period</span>
<div style="border:1px solid #f1f5f9;border-radius:12px;overflow:hidden;margin-bottom:20px;">
  <table>
    <tr><th>Description</th><th style="text-align:right;">Date</th><th style="text-align:right;">Amount</th></tr>
    {income_rows_html}
    <tr class="tfoot"><td style="padding:8px 12px;">Total</td><td></td><td style="padding:8px 12px;color:#15803d;text-align:right;">{currency} {fmt(total_income)}</td></tr>
  </table>
</div>

<!-- 2. Spending by category -->
<span class="section-label">↑ Spending by category</span>
<div style="border:1px solid #f1f5f9;border-radius:12px;overflow:hidden;margin-bottom:20px;">
  <table>
    <tr><th>Category</th><th style="text-align:right;">Txns</th><th style="text-align:right;">Amount</th></tr>
    {cat_rows_html}
    <tr class="tfoot"><td style="padding:8px 12px;">Total</td><td></td><td style="padding:8px 12px;color:#dc2626;text-align:right;">{currency} {fmt(total_expense)}</td></tr>
  </table>
</div>

<!-- 3. Starting next period — fixed commitments -->
<span class="section-label">📅 Starting {next_period_start.strftime('%B')} — fixed commitments</span>
<div style="font-size:12px;color:#92400e;padding:8px 12px;background:#fffbeb;border-radius:8px;border:1px solid #fcd34d;margin-bottom:8px;">
  ℹ️ Your next salary period starts <strong>{next_period_start.strftime('%d %b %Y')}</strong>. These are your known fixed costs — plan your free spending around them.
</div>
<div style="border:1px solid #f1f5f9;border-radius:12px;overflow:hidden;margin-bottom:8px;">
  <table>
    <tr><th>Commitment</th><th style="text-align:right;">Due</th><th style="text-align:right;">Amount</th></tr>
    {commitment_rows_html}
    <tr class="tfoot"><td style="padding:8px 12px;">Total committed</td><td></td><td style="padding:8px 12px;color:#d97706;text-align:right;">{currency} {fmt(total_committed)}</td></tr>
  </table>
</div>
<div style="padding:12px 16px;background:#f0fdf4;border:1px solid #86efac;border-radius:10px;display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
  <div>
    <div style="font-size:13px;font-weight:700;color:#15803d;">Available after fixed costs</div>
    <div style="font-size:11px;color:#16a34a;margin-top:2px;">Based on last period income of {currency} {fmt(total_income)}</div>
  </div>
  <div style="font-size:18px;font-weight:800;color:#15803d;">{currency} {fmt(available_after)}</div>
</div>

<!-- 4. Credit cards -->
<span class="section-label">💳 Credit card balances</span>
<div style="border:1px solid #f1f5f9;border-radius:12px;overflow:hidden;margin-bottom:8px;">
  <table>
    <tr><th>Bank</th><th style="text-align:right;">Limit</th><th style="text-align:right;">Used</th><th style="text-align:right;">Util</th><th style="text-align:right;">Due</th></tr>
    {card_rows_html}
    <tr class="tfoot"><td style="padding:8px 12px;">Total used</td><td></td><td style="padding:8px 12px;color:#dc2626;text-align:right;">{currency} {fmt(total_credit_used)}</td><td></td><td></td></tr>
  </table>
</div>
{card_warnings_html}

<!-- 5. Loans -->
{'<span class="section-label" style="margin-top:20px;display:block;">🏦 Active loans</span><div style="border:1px solid #f1f5f9;border-radius:12px;overflow:hidden;margin-bottom:8px;"><table><tr><th>Loan</th><th style="text-align:right;">Outstanding</th><th style="text-align:right;">Monthly EMI</th></tr>' + loan_rows_html + f'<tr class="tfoot"><td style="padding:8px 12px;">Total outstanding</td><td style="padding:8px 12px;color:#dc2626;text-align:right;">{currency} {fmt(total_outstanding)}</td><td></td></tr></table></div>' if loan_rows else ''}

<!-- 6. Health metrics -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:20px;margin-bottom:24px;">
  <div style="background:#f8fafc;border-radius:12px;padding:14px;border:1px solid #f1f5f9;">
    <div style="font-size:10px;color:#94a3b8;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Savings Rate</div>
    <div style="font-size:22px;font-weight:800;color:#6366f1;">{savings_rate}%</div>
    <div style="height:4px;background:#e2e8f0;border-radius:2px;margin-top:8px;overflow:hidden;"><div style="width:{savings_bar_w}%;height:100%;background:#6366f1;border-radius:2px;"></div></div>
    <div style="font-size:11px;color:#94a3b8;margin-top:5px;">{'Excellent!' if savings_rate >= 20 else 'Good' if savings_rate >= 10 else 'Needs improvement'}</div>
  </div>
  <div style="background:#f8fafc;border-radius:12px;padding:14px;border:1px solid #f1f5f9;">
    <div style="font-size:10px;color:#94a3b8;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Freedom Score</div>
    <div style="font-size:22px;font-weight:800;color:{score_color};">{health.score}/100</div>
    <div style="height:4px;background:#e2e8f0;border-radius:2px;margin-top:8px;overflow:hidden;"><div style="width:{score_bar_w}%;height:100%;background:{score_color};border-radius:2px;"></div></div>
    <div style="font-size:11px;color:#94a3b8;margin-top:5px;">{health.label}</div>
  </div>
</div>

<!-- CTA -->
<div style="text-align:center;">
  <a href="https://brave-grace-production-6691.up.railway.app/dashboard" style="display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:white;text-decoration:none;border-radius:12px;font-size:14px;font-weight:700;">View Full Dashboard</a>
  <div style="font-size:11px;color:#94a3b8;margin-top:10px;">Next summary: 1 {(today.replace(day=1) + timedelta(days=32)).replace(day=1).strftime('%B %Y')}</div>
</div>

</div>

<!-- Footer -->
<div style="padding:16px 24px;border-top:1px solid #f1f5f9;text-align:center;">
  <p style="font-size:11px;color:#94a3b8;margin:4px 0;">FinanceOS · Built in Sri Lanka 🇱🇰 · © 2026</p>
  <p style="font-size:11px;margin:4px 0;">
    <a href="https://brave-grace-production-6691.up.railway.app/privacy" style="color:#6366f1;text-decoration:none;">Privacy</a> ·
    <a href="https://brave-grace-production-6691.up.railway.app/terms" style="color:#6366f1;text-decoration:none;">Terms</a>
  </p>
  <p style="font-size:10px;color:#cbd5e1;margin-top:6px;font-family:serif;">ශ්‍රී ලංකාවේ නිර්මාණය කරන ලදී</p>
</div>

</div></div>
</body>
</html>"""

    send_email(
        user_email,
        f"Your FinanceOS Summary — {period_start.strftime('%B %Y')} 📊",
        html
    )


@app.route("/send-monthly-summary")
@login_required
def trigger_monthly_summary():
    """Manual trigger — for testing. Remove or restrict after launch."""
    user = session.get("user", {})
    send_monthly_summary_email(
        user_id=uid(),
        user_email=user.get("email", ""),
        user_name=user.get("name", "User").split()[0],
    )
    flash("Monthly summary email sent! Check your inbox.", "success")
    return redirect(url_for("dashboard"))



# ─────────────────────────────────────────
#  Monthly Email Scheduler
# ─────────────────────────────────────────
def send_all_monthly_summaries():
    """Send monthly summary to all users — runs on 1st of each month at 8am."""
    with app.app_context():
        try:
            # Get all unique users who have transactions
            user_ids = db.session.query(Transaction.user_id).distinct().all()
            user_ids = [u[0] for u in user_ids if u[0]]

            app.logger.info(f"[Scheduler] Sending monthly summaries to {len(user_ids)} users")

            for user_id in user_ids:
                try:
                    # Get user email from AppSettings or skip
                    # We store email via Auth0 session — look up from transactions
                    # Use preferred name from settings
                    preferred_name = get_setting("preferred_name", "there", user_id=user_id)

                    # Get email from a notification or settings record
                    email_setting = AppSettings.query.filter_by(
                        user_id=user_id, key="email"
                    ).first()

                    # Check if user opted out of monthly email
                    opt_out = AppSettings.query.filter_by(
                        user_id=user_id, key="monthly_email"
                    ).first()
                    opted_in = not opt_out or opt_out.value != "false"

                    if email_setting and email_setting.value and opted_in:
                        send_monthly_summary_email(
                            user_id=user_id,
                            user_email=email_setting.value,
                            user_name=preferred_name,
                        )
                        app.logger.info(f"[Scheduler] Sent summary to {email_setting.value}")
                    else:
                        app.logger.warning(f"[Scheduler] No email found for user {user_id[:20]}")
                except Exception as e:
                    app.logger.error(f"[Scheduler] Error for user {user_id[:20]}: {e}")

        except Exception as e:
            app.logger.error(f"[Scheduler] Fatal error: {e}")


# Start scheduler — only in production (not during Flask reloader child process)
import os
if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    scheduler = BackgroundScheduler(daemon=True)
    # Run on 1st of every month at 8:00am
    scheduler.add_job(
        func=send_all_monthly_summaries,
        trigger=CronTrigger(day=1, hour=8, minute=0),
        id="monthly_summary",
        name="Monthly financial summary email",
        replace_existing=True,
    )
    scheduler.start()
    app.logger.info("[Scheduler] Monthly email scheduler started — runs on 1st of each month at 8am")
    # Shut down cleanly on exit
    atexit.register(lambda: scheduler.shutdown())


@app.route("/toggle-popup", methods=["POST"])
@login_required
def toggle_popup():
    """Toggle the daily summary popup preference."""
    current = get_setting("show_daily_popup", "true", user_id=uid())
    new_val = "false" if current == "true" else "true"
    set_setting("show_daily_popup", new_val, user_id=uid())
    return jsonify({"show": new_val == "true"})


@app.route("/loan/reverse/<int:loan_id>", methods=["POST"])
@login_required
def reverse_loan_payment(loan_id):
    loan = Loan.query.filter_by(id=loan_id, user_id=uid()).first_or_404()

    last_payment = Transaction.query.filter_by(
        user_id=uid(),
        category="Loan Payment"
    ).filter(
        Transaction.description.contains(loan.loan_name)
    ).order_by(Transaction.date.desc(), Transaction.id.desc()).first()

    if not last_payment:
        flash("No payment found to reverse for this loan.", "danger")
        return redirect(url_for("loan_list"))

    amount = last_payment.amount

    # Restore to first wallet
    wallet = Wallet.query.filter_by(user_id=uid()).first()
    if wallet:
        wallet.balance += amount

    # Restore loan balance
    loan.outstanding_balance += amount
    if loan.loan_status == "Paid Off":
        loan.loan_status = "Active"

    loan.next_due_date = loan.next_due_date - relativedelta(months=1)

    db.session.delete(last_payment)
    db.session.commit()

    flash(f"✅ Payment of LKR {amount:,.2f} reversed. Wallet and loan balance restored.", "success")
    return redirect(url_for("loan_list"))



@app.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    """Permanently delete all user data."""
    user_id = uid()
    try:
        # Delete all user data
        for model in [Transaction, FixedExpense, CreditCard, Loan, Wallet,
                      WalletTransfer, NetWorthHistory, BudgetPlanner, Goal,
                      Notification, RecurringPayment, AppSettings, Investment,
                      InvestmentIncome, Debt, FavouriteStock]:
            model.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        session.clear()
        flash("All your data has been permanently deleted.", "success")
        return redirect(url_for("landing"))
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting data: {str(e)}", "danger")
        return redirect(url_for("settings"))


@app.route("/notifications/delete/<int:notif_id>", methods=["POST"])
@login_required
def delete_notification(notif_id):
    n = Notification.query.filter_by(id=notif_id, user_id=uid()).first_or_404()
    db.session.delete(n)
    db.session.commit()
    return redirect(request.referrer or url_for("notifications"))


# ─────────────────────────────────────────
#  Analytics Dashboard Customization
# ─────────────────────────────────────────

ANALYTICS_WIDGETS_DEFAULT = [
    {"id": "health",       "label": "Financial Health",       "visible": True,  "order": 1},
    {"id": "stats",        "label": "Key Stats",              "visible": True,  "order": 2},
    {"id": "monthly",      "label": "Monthly Trend Chart",    "visible": True,  "order": 3},
    {"id": "savings",      "label": "Savings Trend",          "visible": True,  "order": 4},
    {"id": "categories",   "label": "Top Categories",         "visible": True,  "order": 5},
    {"id": "daily",        "label": "Daily Spending",         "visible": True,  "order": 6},
    {"id": "income",       "label": "Income Sources",         "visible": True,  "order": 7},
    {"id": "budget",       "label": "Budget vs Actual",       "visible": True,  "order": 8},
    {"id": "largest",      "label": "Largest Expenses",       "visible": True,  "order": 9},
    {"id": "weekday",      "label": "Weekday vs Weekend",     "visible": True,  "order": 10},
    {"id": "heatmap",      "label": "Spending Heatmap",       "visible": True,  "order": 11},
    {"id": "fixed",        "label": "Fixed Commitments",      "visible": True,  "order": 12},
]

def get_analytics_widgets(user_id):
    import json
    raw = get_setting("analytics_widgets", None, user_id=user_id)
    if raw:
        try:
            saved = json.loads(raw)
            # Merge with defaults to catch any new widgets added later
            saved_ids = {w["id"] for w in saved}
            for default in ANALYTICS_WIDGETS_DEFAULT:
                if default["id"] not in saved_ids:
                    saved.append({**default, "order": len(saved) + 1})
            return sorted(saved, key=lambda x: x["order"])
        except Exception:
            pass
    return ANALYTICS_WIDGETS_DEFAULT.copy()


@app.route("/analytics/save-widgets", methods=["POST"])
@login_required
def save_analytics_widgets():
    import json
    data = request.get_json()
    widgets = data.get("widgets", [])
    set_setting("analytics_widgets", json.dumps(widgets), user_id=uid())
    db.session.commit()
    return jsonify({"saved": True})


# ─────────────────────────────────────────
#  Crypto
# ─────────────────────────────────────────
@app.route("/crypto")
def crypto_page():
    import requests as http
    top_coins = []
    error = None
    try:
        r = http.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency":"usd","order":"market_cap_desc","per_page":20,"page":1,"price_change_percentage":"24h"},
            headers={"User-Agent":"FinanceOS/1.0"}, timeout=10,
        )
        if r.status_code == 200:
            top_coins = r.json()
        else:
            error = "api_down"
    except Exception:
        error = "api_down"
    wallets_list = Wallet.query.filter_by(user_id=uid()).all() if uid() else []
    crypto_investments = Investment.query.filter_by(user_id=uid(), asset_type="Crypto", status="active").all() if uid() else []
    currency = get_setting("currency_symbol", "LKR", user_id=uid()) if uid() else "LKR"
    return render_template("crypto.html",
        top_coins=top_coins,
        crypto_investments=crypto_investments,
        error=error,
        currency_symbol=currency,
        wallets=wallets_list,
        is_logged_in=uid() is not None,
    )


@app.route("/crypto/prices", methods=["POST"])
@login_required
def crypto_prices():
    import requests as http
    data = request.get_json()
    coin_ids = data.get("ids", [])
    if not coin_ids:
        return jsonify({})
    try:
        r = http.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids":",".join(coin_ids),"vs_currencies":"usd","include_24hr_change":"true"},
            headers={"User-Agent":"FinanceOS/1.0"}, timeout=10,
        )
        if r.status_code == 200:
            return jsonify(r.json())
    except Exception:
        pass
    return jsonify({})


@app.route("/crypto/update-portfolio-prices", methods=["POST"])
@login_required
def crypto_update_portfolio_prices():
    import requests as http
    SYMBOL_TO_ID = {
        "BTC":"bitcoin","ETH":"ethereum","SOL":"solana","BNB":"binancecoin",
        "XRP":"ripple","ADA":"cardano","DOGE":"dogecoin","DOT":"polkadot",
        "MATIC":"matic-network","LINK":"chainlink","LTC":"litecoin",
        "AVAX":"avalanche-2","ATOM":"cosmos","TRX":"tron","SHIB":"shiba-inu",
    }
    investments = Investment.query.filter_by(user_id=uid(), asset_type="Crypto", status="active").all()
    if not investments:
        return jsonify({"updated": 0})
    id_to_inv = {}
    for inv in investments:
        sym = (inv.symbol or "").upper()
        coin_id = SYMBOL_TO_ID.get(sym) or inv.name.lower().replace(" ", "-")
        id_to_inv[coin_id] = inv
    try:
        r = http.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids":",".join(id_to_inv.keys()),"vs_currencies":"usd","include_24hr_change":"true"},
            headers={"User-Agent":"FinanceOS/1.0"}, timeout=12,
        )
        if r.status_code != 200:
            return jsonify({"error":"API unavailable","updated":0})
        prices = r.json()
        updated = 0
        result = {}
        for coin_id, inv in id_to_inv.items():
            info = prices.get(coin_id, {})
            price = info.get("usd", 0)
            chg = info.get("usd_24h_change", 0)
            if price and price > 0:
                inv.current_price = price
                updated += 1
                result[inv.id] = {"price":price,"change":round(chg,2) if chg else 0,"value":round(inv.current_value,0)}
        if updated > 0:
            db.session.commit()
            update_networth_snapshot(user_id=uid())
        return jsonify({"updated":updated,"prices":result})
    except Exception as e:
        return jsonify({"error":str(e),"updated":0})


@app.route("/investments/edit/<int:inv_id>", methods=["POST"])
@login_required
def edit_investment(inv_id):
    inv = Investment.query.filter_by(id=inv_id, user_id=uid()).first_or_404()
    inv.name = request.form.get("name", inv.name).strip()
    inv.symbol = request.form.get("symbol", inv.symbol or "").strip().upper()
    inv.institution = request.form.get("institution", inv.institution or "").strip()
    inv.units = float(request.form.get("units") or inv.units)
    inv.purchase_price = float(request.form.get("purchase_price") or inv.purchase_price)
    inv.current_price = float(request.form.get("current_price") or inv.current_price)
    inv.interest_rate = float(request.form.get("interest_rate") or inv.interest_rate or 0)
    if request.form.get("purchase_date"):
        inv.purchase_date = datetime.strptime(request.form["purchase_date"], "%Y-%m-%d").date()
    if request.form.get("maturity_date"):
        inv.maturity_date = datetime.strptime(request.form["maturity_date"], "%Y-%m-%d").date()
    inv.notes = request.form.get("notes", inv.notes or "").strip()
    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash(f"✅ {inv.name} updated.", "success")
    return redirect(url_for("investments"))


@app.route("/income/edit/<int:income_id>", methods=["POST"])
@login_required
def edit_income(income_id):
    txn = Transaction.query.filter_by(id=income_id, user_id=uid(), trans_type='income').first_or_404()
    old_amount = txn.amount
    old_wallet_id = txn.wallet_id
    new_amount = float(request.form.get('amount') or old_amount)
    txn.description = request.form.get('description', txn.description).strip()
    txn.category = request.form.get('category', txn.category)
    txn.amount = new_amount
    if request.form.get('date'):
        txn.date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
    new_wallet_id = int(request.form['wallet_id']) if request.form.get('wallet_id') else None
    if old_wallet_id:
        old_wallet = Wallet.query.get(old_wallet_id)
        if old_wallet:
            old_wallet.balance -= old_amount
    if new_wallet_id:
        new_wallet = Wallet.query.get(new_wallet_id)
        if new_wallet:
            new_wallet.balance += new_amount
    txn.wallet_id = new_wallet_id
    db.session.commit()
    update_networth_snapshot(user_id=uid())
    flash('Income updated.', 'success')
    return redirect(url_for('income'))


@app.route("/migrate-to-email")
def migrate_to_email():
    email = "hiroshann@gmail.com"
    old_ids = [
        "auth0|6a1d58287f1bf67cfc7e872d",
        "google-oauth2|103051075859000420599"
    ]
    models_list = [Transaction, Wallet, CreditCard, Loan, Goal,
                   BudgetPlanner, FixedExpense, RecurringPayment,
                   Investment, InvestmentIncome, Debt, AppSettings,
                   Notification, NetWorthHistory, FavouriteStock]
    total = 0
    for old_id in old_ids:
        for model in models_list:
            try:
                updated = model.query.filter_by(user_id=old_id).update({"user_id": email})
                total += updated
            except Exception:
                pass
    db.session.commit()
    return f"✅ Migrated {total} records to {email}"

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)