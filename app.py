from flask import Flask, render_template, request, redirect, flash, url_for, g, jsonify, Response, session
from models import db, Transaction, FixedExpense, CreditCard, Loan, Wallet, WalletTransfer, \
    NetWorthHistory, BudgetPlanner, Goal, Notification, RecurringPayment, AppSettings
import requests as req_lib
from calendar import monthrange
from datetime import datetime, date, timedelta, time
from dateutil.relativedelta import relativedelta
import json, csv, io, os
from collections import defaultdict
from flask_migrate import Migrate
from finance_service import (add_transaction, update_networth_snapshot, check_and_create_notifications,
                              apply_due_recurring_payments, get_spending_insights, get_financial_health_score,
                              get_setting, set_setting)
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from functools import wraps

load_dotenv()

base_dir = os.path.abspath(os.path.dirname(__file__))
# Use PostgreSQL from env, fallback to SQLite for local dev without .env
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///budget.db')
# Fix for older Heroku/Railway URLs that use postgres:// instead of postgresql://
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,       # reconnect if connection dropped
    'pool_recycle': 300,         # recycle connections every 5 min
}
app.secret_key = os.getenv('APP_SECRET_KEY', 'fallback_dev_secret_change_this')

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
    dark_mode = get_setting("dark_mode", "false", user_id=user_id) == "true"
    currency_symbol = get_setting("currency_symbol", "LKR", user_id=user_id)
    return dict(
        unread_notifications=unread_count,
        dark_mode=dark_mode,
        currency_symbol=currency_symbol,
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


@app.route('/callback')
def callback():
    token = auth0.authorize_access_token()
    userinfo = token.get('userinfo')
    session['user'] = {
        'id': userinfo['sub'],
        'name': userinfo.get('name', 'User'),
        'email': userinfo.get('email', ''),
        'picture': userinfo.get('picture', '')
    }
    flash(f"Welcome back, {session['user']['name'].split()[0]}! 👋", "success")
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

    start_of_month = datetime.combine(today.replace(day=1), time.min)
    end_of_today = datetime.combine(today, time.max)

    # Show all transactions this period including future-dated ones
    this_month_transactions = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.date >= start_of_month
    ).order_by(Transaction.date.desc()).all()

    # Separate future transactions for visual indicator
    future_txn_ids = set(
        t.id for t in this_month_transactions
        if (t.date.date() if isinstance(t.date, datetime) else t.date) > today
    )

    transactions = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.date >= period_start,
        Transaction.date <= period_end
    ).all()

    income_by_category = defaultdict(float)
    expense_by_category = defaultdict(float)
    total_income = 0
    total_expense = 0

    for t in transactions:
        category = t.category or t.description or "Other"
        if t.trans_type == "income":
            total_income += t.amount
            income_by_category[category] += t.amount
        elif t.trans_type == "expense":
            total_expense += t.amount
            expense_by_category[category] += t.amount

    previous_transactions = Transaction.query.filter(Transaction.user_id == uid(), Transaction.date < period_start).all()
    carryover_balance = sum(t.amount if t.trans_type == "income" else -t.amount for t in previous_transactions)
    current_balance = carryover_balance + total_income - total_expense
    # Total Wallet Balance = actual wallet balances (source of truth)
    total_wallet_balance = sum(w.balance for w in wallets)

    chart_data = {
        "income": dict(income_by_category),
        "expense": dict(expense_by_category),
        "balance": current_balance
    }

    # Names already paid this period
    paid_names_period = set(t.description for t in transactions if t.trans_type == 'expense')

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

    # Show only UNPAID, sorted by due date (overdue first, then upcoming)
    upcoming_fixed_sorted = sorted(
        [p for p in upcoming_payments if not p["paid"]], key=lambda x: x["due_date"]
    )[:5]

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
                "date": exp.date, "repeat": False
            })

    # Add loan installments to next period forecast
    for l in Loan.query.filter_by(user_id=uid(), loan_status='Active').all():
        next_month_expenses.append({
            "id": l.id, "name": f"Loan: {l.loan_name}",
            "amount": l.monthly_payment,
            "date": next_period_start,
            "repeat": True, "category": "Loan Payment"
        })
    # Add credit card minimum payments to next period forecast
    for c in CreditCard.query.filter_by(user_id=uid()).all():
        if c.due_date:
            next_month_expenses.append({
                "id": c.id, "name": f"{c.bank_name} Card Min.",
                "amount": c.minimum_payment,
                "date": c.due_date,
                "repeat": True, "category": "Credit Card"
            })

    next_month_total = sum(e["amount"] for e in next_month_expenses)

    calendar_expenses = defaultdict(list)
    for e in next_month_expenses:
        calendar_expenses[e["date"].day].append(e)

    salary_period_str = f"{period_start.strftime('%d %b %Y')} - {period_end.strftime('%d %b %Y')}"

    health = get_financial_health_score(user_id=uid())
    active_goals = Goal.query.filter_by(status='active', user_id=uid()).limit(3).all()
    recent_notifications = Notification.query.filter_by(is_read=False, user_id=uid()).order_by(
        Notification.created_at.desc()).limit(5).all()

    return render_template(
        "dashboard.html",
        wallets=wallets,
        total_income=total_income,
        total_expense=total_expense,
        balance=current_balance,
        upcoming_payments=upcoming_fixed_sorted,
        chart_data=chart_data,
        fixed_chart_data=dict(fixed_chart_data),
        monthly_trends=json.dumps({"labels": monthly_labels, "expenses": monthly_values, "income": monthly_income_values}),
        selected_month=period_start.strftime("%Y-%m"),
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
        this_month_transactions=this_month_transactions,
        future_txn_ids=future_txn_ids,
        total_wallet_balance=total_wallet_balance,
        salary_period=salary_period_str,
        today=today,
        health=health,
        active_goals=active_goals,
        recent_notifications=recent_notifications,
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
def delete_transaction(transaction_id):
    tr = Transaction.query.filter_by(id=transaction_id, user_id=uid()).first_or_404()
    db.session.delete(tr)
    db.session.commit()
    flash(f"Transaction '{tr.description}' deleted.", "success")
    return redirect(request.referrer or url_for('dashboard'))


@app.route('/edit_transaction/<int:transaction_id>', methods=['GET', 'POST'])
def edit_transaction(transaction_id):
    tr = Transaction.query.filter_by(id=transaction_id, user_id=uid()).first_or_404()
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    cards = CreditCard.query.filter_by(user_id=uid()).all()
    if request.method == 'POST':
        tr.description = request.form['description']
        tr.amount = float(request.form['amount'])
        tr.category = request.form.get('category', tr.category)
        tr.trans_type = request.form['trans_type']
        tr.notes = request.form.get('notes', '')
        date_str = request.form.get('date')
        if date_str:
            tr.date = datetime.strptime(date_str, '%Y-%m-%d')
        db.session.commit()
        flash("Transaction updated.", "success")
        return redirect(url_for('transactions'))
    return render_template('edit_transaction.html', transaction=tr, wallets=wallets, cards=cards)


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

    return render_template(
        'income.html',
        income_list=income_list,
        total_income=total_income,
        this_month_income_list=this_month_income_list,
        this_month_income_total=this_month_income_total,
        wallets=wallets,
        now=datetime.now
    )


@app.route('/delete_income/<int:income_id>', methods=['POST'])
def delete_income(income_id):
    income = Transaction.query.filter_by(id=income_id, user_id=uid()).first_or_404()
    db.session.delete(income)
    db.session.commit()
    flash('Income deleted.', 'info')
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
    db.session.delete(wallet)
    db.session.commit()
    flash("Wallet deleted!", "success")
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
#  Fixed Expenses
# ─────────────────────────────────────────
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

    all_expenses = FixedExpense.query.filter_by(user_id=uid()).order_by(FixedExpense.date).all()
    fixed_recurring, monthly_recurring, fixed_this_month, next_month_expenses = [], [], [], []
    fixed_chart_data = defaultdict(float)

    for exp in all_expenses:
        repeat_until = exp.repeat_until or date.max
        if not exp.repeat and exp.repeat_until:
            fixed_recurring.append(exp)
        if exp.repeat:
            next_occurrence = get_next_occurrence(exp.date, current_month_start)
            if next_occurrence <= repeat_until:
                monthly_recurring.append({
                    "id": exp.id, "name": exp.name, "amount": exp.amount,
                    "date": next_occurrence, "repeat": True,
                    "repeat_until": exp.repeat_until, "category": exp.category or "Uncategorized"
                })
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
        if (exp.repeat or exp.repeat_until) and repeat_until >= next_month_start:
            next_due = get_next_occurrence(exp.date, next_month_start)
            if next_month_start <= next_due <= next_month_end and next_due <= repeat_until:
                next_month_expenses.append({
                    "id": exp.id, "name": exp.name, "amount": exp.amount,
                    "date": next_due, "repeat": exp.repeat,
                    "repeat_until": exp.repeat_until, "category": exp.category or "Uncategorized"
                })
        if exp.repeat:
            due = get_next_occurrence(exp.date, current_month_start)
            if current_month_start <= due <= current_month_end and due <= repeat_until:
                fixed_chart_data[exp.category or "Uncategorized"] += exp.amount
        elif not exp.repeat and current_month_start <= exp.date <= current_month_end:
            fixed_chart_data[exp.category or "Uncategorized"] += exp.amount

    wallets = Wallet.query.filter_by(user_id=uid()).all()
    credit_cards = CreditCard.query.filter_by(user_id=uid()).all()

    return render_template(
        'fixed_expenses.html',
        repeat_until_expenses=fixed_recurring,
        repeating_expenses=monthly_recurring,
        upcoming_fixed=fixed_this_month,
        next_month_expenses=next_month_expenses,
        selected_month=current_month_start.strftime("%Y-%m"),
        current_month=current_month_start,
        month_start=current_month_start,
        month_end=current_month_end,
        fixed_chart_data=dict(fixed_chart_data),
        total_repeat_until=sum(e.amount for e in fixed_recurring),
        total_monthly_repeats=sum(e["amount"] for e in monthly_recurring),
        total_this_month=sum(e["amount"] for e in fixed_this_month),
        paid_this_month=paid_names_fe,
        relativedelta=relativedelta,
        wallets=wallets,
        credit_cards=credit_cards,
    )


@app.route('/add-fixed', methods=['POST'])
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
def apply_fixed(expense_id):
    fixed_exp = FixedExpense.query.filter_by(id=expense_id, user_id=uid()).first_or_404()
    today = date.today()
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
        return redirect(request.referrer or url_for("dashboard"))

    payment_method = request.form.get('payment_method')
    wallet_id, credit_id = None, None
    if payment_method and payment_method.startswith("wallet_"):
        wallet_id = int(payment_method.split("_")[1])
    elif payment_method and payment_method.startswith("credit_"):
        credit_id = int(payment_method.split("_")[1])

    add_transaction(
        amount=fixed_exp.amount, description=fixed_exp.name,
        trans_type="expense", wallet_id=wallet_id, linked_credit=credit_id,
        date_obj=today, category=fixed_exp.category, user_id=uid()
    )
    flash(f"✅ {fixed_exp.name} applied.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route('/delete_fixed/<int:expense_id>', methods=['POST'])
def delete_fixed(expense_id):
    expense = FixedExpense.query.filter_by(id=expense_id, user_id=uid()).first_or_404()
    db.session.delete(expense)
    db.session.commit()
    flash("Fixed expense deleted.", "success")
    return redirect(request.referrer or url_for('fixed_expenses'))


@app.route("/mark-fixed-paid/<int:expense_id>", methods=["POST"])
def mark_fixed_paid(expense_id):
    exp = FixedExpense.query.filter_by(id=expense_id, user_id=uid()).first_or_404()
    today = date.today()
    month_start = today.replace(day=1)

    already = Transaction.query.filter(
        Transaction.description == exp.name,
        Transaction.trans_type == "expense",
        Transaction.date >= month_start,
        Transaction.date <= today
    ).first()
    if already:
        flash(f"⚠️ '{exp.name}' already paid this month on {already.date.strftime('%d %b')}.", "warning")
        return redirect(url_for("dashboard"))

    add_transaction(
        amount=exp.amount, description=exp.name, trans_type="expense",
        date_obj=today, category=exp.category, user_id=uid()
    )
    flash(f"✅ {exp.name} marked as paid.", "success")
    return redirect(url_for("dashboard"))


# ─────────────────────────────────────────
#  Expense Scheduler
# ─────────────────────────────────────────
@app.route('/expense-scheduler')
@login_required
def expense_scheduler():
    today = date.today()
    current_month_start = today.replace(day=1)
    next_month_start = current_month_start + relativedelta(months=1)
    next_month_end = next_month_start + relativedelta(months=1) - timedelta(days=1)

    all_expenses = FixedExpense.query.filter_by(user_id=uid()).order_by(FixedExpense.date).all()

    def get_next_occurrence(start_date, after_date):
        next_date = start_date
        while next_date < after_date:
            next_date += relativedelta(months=1)
        return next_date

    upcoming_fixed, next_month_expenses = [], []
    month_end = current_month_start + relativedelta(months=1) - timedelta(days=1)

    for exp in all_expenses:
        if exp.repeat_until and exp.repeat_until < exp.date:
            continue
        repeat_until = exp.repeat_until or date.max
        if exp.repeat and repeat_until >= today:
            next_occurrence = get_next_occurrence(exp.date, today)
            if current_month_start <= next_occurrence <= month_end:
                upcoming_fixed.append({
                    "id": exp.id, "name": exp.name, "amount": exp.amount,
                    "due_date": next_occurrence.strftime('%Y-%m-%d'),
                    "repeat": True, "repeat_until": exp.repeat_until,
                    "category": exp.category or "Uncategorized"
                })
            next_month_occurrence = get_next_occurrence(exp.date, next_month_start)
            if next_month_start <= next_month_occurrence <= next_month_end and next_month_occurrence <= repeat_until:
                next_month_expenses.append({
                    "id": exp.id, "name": exp.name, "amount": exp.amount,
                    "date": next_month_occurrence, "repeat": True,
                    "repeat_until": exp.repeat_until, "category": exp.category or "Uncategorized"
                })
        else:
            if current_month_start <= exp.date <= month_end:
                upcoming_fixed.append({
                    "id": exp.id, "name": exp.name, "amount": exp.amount,
                    "due_date": exp.date.strftime('%Y-%m-%d'),
                    "repeat": False, "repeat_until": exp.repeat_until,
                    "category": exp.category or "Uncategorized"
                })
            if next_month_start <= exp.date <= next_month_end:
                next_month_expenses.append({
                    "id": exp.id, "name": exp.name, "amount": exp.amount,
                    "date": exp.date, "repeat": False,
                    "repeat_until": exp.repeat_until, "category": exp.category or "Uncategorized"
                })

    # Add loan + card installments to scheduler
    loans_active = Loan.query.filter_by(user_id=uid(), loan_status='Active').all()
    cards_active = CreditCard.query.filter_by(user_id=uid()).all()
    for l in loans_active:
        if l.next_due_date and current_month_start <= l.next_due_date <= month_end:
            upcoming_fixed.append({
                "id": l.id, "name": f"Loan: {l.loan_name}",
                "amount": l.monthly_payment,
                "due_date": l.next_due_date.strftime('%Y-%m-%d'),
                "repeat": True, "category": "Loan Payment"
            })
    for c in cards_active:
        if c.due_date and current_month_start <= c.due_date <= month_end:
            upcoming_fixed.append({
                "id": c.id, "name": f"{c.bank_name} Card",
                "amount": c.minimum_payment,
                "due_date": c.due_date.strftime('%Y-%m-%d'),
                "repeat": True, "category": "Credit Card"
            })

    total_this_month_all = sum(e["amount"] for e in upcoming_fixed)
    total_next_month_all = sum(e["amount"] for e in next_month_expenses)

    return render_template(
        'expense_scheduler.html',
        upcoming_fixed=sorted(upcoming_fixed, key=lambda x: x["due_date"]),
        next_month_expenses=sorted(next_month_expenses, key=lambda x: str(x.get("due_date", x.get("date", "")))),
        total_this_month=total_this_month_all,
        total_next_month=total_next_month_all,
    )


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

    return render_template(
        "budget_planner.html",
        budget_summary=budget_summary,
        budgets=budgets,
        month=current_month,
        total_budgeted=total_budgeted,
        total_spent=total_spent
    )


@app.route("/delete_budget/<int:budget_id>", methods=["POST"])
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
        return redirect(url_for("recurring_payments"))

    recs = RecurringPayment.query.filter_by(user_id=uid()).order_by(RecurringPayment.next_date).all()
    wallets = Wallet.query.filter_by(user_id=uid()).all()
    cards = CreditCard.query.filter_by(user_id=uid()).all()
    today = date.today()
    total_monthly = sum(
        r.amount for r in recs if r.is_active and r.frequency == "monthly"
    )
    return render_template("recurring.html", recurring=recs, wallets=wallets,
                           cards=cards, today=today, total_monthly=total_monthly)


@app.route("/recurring/delete/<int:rec_id>", methods=["POST"])
def delete_recurring(rec_id):
    rec = RecurringPayment.query.filter_by(id=rec_id, user_id=uid()).first_or_404()
    db.session.delete(rec)
    db.session.commit()
    flash("Recurring payment deleted.", "info")
    return redirect(url_for("recurring_payments"))


@app.route("/recurring/apply/<int:rec_id>", methods=["POST"])
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
    return redirect(url_for("recurring_payments"))


# ─────────────────────────────────────────
#  Analytics (NEW)
# ─────────────────────────────────────────
@app.route("/analytics")
@login_required
def analytics():
    today = date.today()
    period = request.args.get("period", "month")

    if period == "week":
        start = today - timedelta(days=7)
    elif period == "quarter":
        start = today - relativedelta(months=3)
    elif period == "year":
        start = today - relativedelta(years=1)
    else:
        start = today.replace(day=1)

    insights = get_spending_insights(start, today, user_id=uid())
    health = get_financial_health_score(user_id=uid())

    # Month-over-month last 12 months
    monthly_data = []
    for i in range(11, -1, -1):
        m_start = (today - relativedelta(months=i)).replace(day=1)
        m_end = m_start + relativedelta(months=1) - timedelta(days=1)
        txns = Transaction.query.filter(
            Transaction.date >= m_start, Transaction.date <= m_end
        ).all()
        income = sum(t.amount for t in txns if t.trans_type == "income")
        expense = sum(t.amount for t in txns if t.trans_type == "expense")
        monthly_data.append({
            "month": m_start.strftime("%b %Y"),
            "income": income,
            "expense": expense,
            "savings": income - expense
        })

    # Top categories last 3 months
    three_ago = today - relativedelta(months=3)
    cat_txns = Transaction.query.filter(
        Transaction.user_id == uid(),
        Transaction.trans_type == "expense",
        Transaction.date >= three_ago
    ).all()
    cat_totals = defaultdict(float)
    for t in cat_txns:
        cat_totals[t.category or "Other"] += t.amount
    top_categories = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)[:8]

    # Weekday vs Weekend spending
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
    weekday_count = max(1, sum(1 for t in all_txns_period
        if (t.date.date() if isinstance(t.date, datetime) else t.date).weekday() < 5))
    weekend_count = max(1, sum(1 for t in all_txns_period
        if (t.date.date() if isinstance(t.date, datetime) else t.date).weekday() >= 5))
    weekday_avg = weekday_total / weekday_count
    weekend_avg = weekend_total / weekend_count

    # This month fixed total needed
    this_month_fixed_total = sum(
        e["amount"] for e in []  # calculated in fixed_expenses route
    )
    loans_active = Loan.query.filter_by(user_id=uid(), loan_status='Active').all()
    cards_active = CreditCard.query.filter_by(user_id=uid()).all()
    fixed_committed = (
        sum(l.monthly_payment for l in loans_active) +
        sum(c.minimum_payment for c in cards_active)
    )

    return render_template(
        "analytics.html",
        insights=insights,
        health=health,
        monthly_data=monthly_data,
        top_categories=top_categories,
        period=period,
        start_date=start,
        end_date=today,
        weekday_avg=weekday_avg,
        weekend_avg=weekend_avg,
        weekday_total=weekday_total,
        weekend_total=weekend_total,
        fixed_committed=fixed_committed,
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
        set_setting("salary_cycle_day", request.form.get("salary_cycle_day", "25"), user_id=uid())
        set_setting("currency_symbol", request.form.get("currency_symbol", "LKR"), user_id=uid())
        dark = "on" if "dark_mode" in request.form else "false"
        set_setting("dark_mode", "true" if dark == "on" else "false", user_id=uid())
        flash("Settings saved!", "success")
        return redirect(url_for("settings"))

    return render_template("settings.html",
                           salary_cycle_day=get_setting("salary_cycle_day", "25"),
                           currency_symbol=get_setting("currency_symbol", "LKR"),
                           dark_mode=get_setting("dark_mode", "false") == "true")


# ─────────────────────────────────────────
#  Export (NEW)
# ─────────────────────────────────────────
@app.route("/export/transactions")
def export_transactions():
    start_str = request.args.get("start")
    end_str = request.args.get("end")
    query = Transaction.query
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
#  Financial Tools
# ─────────────────────────────────────────
@app.route("/tools")
@login_required
def financial_tools():
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
    return render_template("financial_tools.html",
        wallets=wallets, cards=cards, loans=loans,
        total_monthly_commitments=total_monthly_commitments,
        monthly_income_avg=monthly_income_avg,
        total_savings=sum(w.balance for w in wallets),
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
