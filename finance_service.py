from datetime import datetime, date, timedelta, time
from dateutil.relativedelta import relativedelta
from collections import defaultdict
from models import db, Transaction, Wallet, CreditCard, Loan, NetWorthHistory, Notification, Goal, RecurringPayment, AppSettings


def get_setting(key, default=None, user_id=None):
    q = AppSettings.query.filter_by(key=key)
    if user_id:
        q = q.filter_by(user_id=user_id)
    s = q.first()
    return s.value if s else default


def set_setting(key, value, user_id=None):
    q = AppSettings.query.filter_by(key=key)
    if user_id:
        q = q.filter_by(user_id=user_id)
    s = q.first()
    if s:
        s.value = str(value)
    else:
        s = AppSettings(key=key, value=str(value), user_id=user_id)
        db.session.add(s)
    db.session.commit()


def add_transaction(amount, description, trans_type, wallet_id=None, linked_credit=None,
                    linked_loan=None, currency="LKR", date_obj=None, category=None, notes=None, tags=None, user_id=None):
    txn = Transaction(
        amount=amount,
        description=description,
        trans_type=trans_type,
        category=category or description,
        notes=notes,
        tags=tags,
        date=date_obj or datetime.now().date(),
        user_id=user_id
    )
    db.session.add(txn)

    # Only affect balances for past/today transactions, not future
    txn_date = txn.date if isinstance(txn.date, date) else txn.date.date()
    is_due = txn_date <= date.today()

    if wallet_id and is_due:
        wallet = Wallet.query.get(wallet_id)
        if wallet:
            if trans_type == "expense":
                wallet.balance -= amount
            elif trans_type == "income":
                wallet.balance += amount

    if linked_credit and is_due:
        card = CreditCard.query.get(linked_credit)
        if card:
            card.available_balance -= amount
            if card.available_balance < 0:
                card.available_balance = 0

    if linked_loan and is_due:
        loan = Loan.query.get(linked_loan)
        if loan:
            loan.outstanding_balance -= amount
            if loan.outstanding_balance < 0:
                loan.outstanding_balance = 0

    update_networth_snapshot()
    check_and_create_notifications()
    db.session.commit()
    return txn


def update_networth_snapshot(user_id=None):
    today = date.today()
    wallets = Wallet.query.filter_by(user_id=user_id).all() if user_id else Wallet.query.all()
    loans = Loan.query.filter_by(user_id=user_id).all() if user_id else Loan.query.all()
    cards = CreditCard.query.filter_by(user_id=user_id).all() if user_id else CreditCard.query.all()

    total_assets = sum(w.balance for w in wallets)
    total_loans = sum(l.outstanding_balance for l in loans)
    total_cards = sum(c.credit_limit - c.available_balance for c in cards)
    total_liabilities = total_loans + total_cards
    net_worth = total_assets - total_liabilities

    existing = NetWorthHistory.query.filter_by(date=today, user_id=user_id).first()
    if not existing:
        snapshot = NetWorthHistory(
            date=today, user_id=user_id,
            total_assets=total_assets,
            total_liabilities=total_liabilities,
            net_worth=net_worth
        )
        db.session.add(snapshot)
    else:
        existing.total_assets = total_assets
        existing.total_liabilities = total_liabilities
        existing.net_worth = net_worth

    db.session.commit()


def check_and_create_notifications(user_id=None):
    today = date.today()
    soon = today + timedelta(days=7)
    # Use today as a key — only create ONE notification per item per day
    today_str = today.strftime('%Y-%m-%d')

    def already_notified(related_type, related_id, title_contains):
        """Check if this notification already exists — ever (not just today).
        Prevents duplicates accumulating over time."""
        from datetime import datetime, time, timedelta
        # Check last 30 days to avoid spam but allow re-notification after a month
        thirty_days_ago = datetime.combine(today - timedelta(days=30), time(0, 0))
        return Notification.query.filter(
            Notification.user_id == user_id,
            Notification.related_type == related_type,
            Notification.related_id == related_id,
            Notification.title.contains(title_contains),
            Notification.created_at >= thirty_days_ago,
        ).first() is not None

    # Loan due soon
    for loan in Loan.query.filter_by(loan_status='Active', user_id=user_id).all():
        if loan.next_due_date and today <= loan.next_due_date <= soon:
            if not already_notified("loan", loan.id, "Loan payment"):
                db.session.add(Notification(
                    title="Loan payment due soon",
                    message=f"{loan.loan_name} — {loan.monthly_payment:,.0f} due on {loan.next_due_date.strftime('%d %b %Y')}",
                    notif_type="warning",
                    related_type="loan",
                    related_id=loan.id,
                    user_id=user_id
                ))

    # Credit card due soon
    for card in CreditCard.query.filter_by(user_id=user_id).all():
        if card.due_date and today <= card.due_date <= soon:
            if not already_notified("credit_card", card.id, "Credit card payment"):
                db.session.add(Notification(
                    title="Credit card payment due",
                    message=f"{card.bank_name} — min {card.minimum_payment:,.0f} due on {card.due_date.strftime('%d %b %Y')}",
                    notif_type="warning",
                    related_type="credit_card",
                    related_id=card.id,
                    user_id=user_id
                ))

        # High utilization
        if card.credit_limit and card.credit_limit > 0:
            utilization = (card.credit_limit - card.available_balance) / card.credit_limit * 100
            if utilization > 80:
                if not already_notified("credit_card", card.id, "utilization"):
                    db.session.add(Notification(
                        title="High credit utilization",
                        message=f"{card.bank_name} at {utilization:.0f}% utilization. Consider paying down.",
                        notif_type="danger",
                        related_type="credit_card",
                        related_id=card.id,
                        user_id=user_id
                    ))

    db.session.commit()


def apply_due_recurring_payments(user_id=None):
    """Auto-apply recurring payments that are due."""
    today = date.today()
    applied = []
    for rec in RecurringPayment.query.filter_by(is_active=True, auto_apply=True, user_id=user_id).all():
        if rec.next_date <= today:
            if rec.end_date and rec.end_date < today:
                rec.is_active = False
                continue
            txn = add_transaction(
                amount=rec.amount,
                description=rec.name,
                trans_type="expense",
                wallet_id=rec.wallet_id,
                linked_credit=rec.credit_card_id,
                date_obj=rec.next_date,
                category=rec.category,
                user_id=user_id
            )
            rec.last_applied = rec.next_date
            # Advance next date
            freq_map = {
                "weekly": timedelta(weeks=1),
                "biweekly": timedelta(weeks=2),
                "monthly": relativedelta(months=1),
                "quarterly": relativedelta(months=3),
                "yearly": relativedelta(years=1),
            }
            delta = freq_map.get(rec.frequency, relativedelta(months=1))
            rec.next_date = rec.next_date + delta
            applied.append(rec.name)

    db.session.commit()
    return applied


def get_spending_insights(period_start, period_end, user_id=None):
    q = [Transaction.trans_type == "expense",
         Transaction.date >= period_start,
         Transaction.date <= period_end]
    if user_id:
        q.append(Transaction.user_id == user_id)
    transactions = Transaction.query.filter(*q).all()

    by_category = defaultdict(float)
    by_day = defaultdict(float)
    total = 0

    for t in transactions:
        cat = t.category or t.description or "Other"
        by_category[cat] += t.amount
        day_key = t.date.strftime("%Y-%m-%d") if isinstance(t.date, date) else t.date.date().strftime("%Y-%m-%d")
        by_day[day_key] += t.amount
        total += t.amount

    sorted_cats = sorted(by_category.items(), key=lambda x: x[1], reverse=True)
    top_category = sorted_cats[0][0] if sorted_cats else "N/A"
    avg_daily = total / max(1, (period_end - period_start).days + 1)

    # Previous period comparison
    delta = period_end - period_start
    prev_start = period_start - (delta + timedelta(days=1))
    prev_end = period_start - timedelta(days=1)
    pq = [Transaction.trans_type == "expense",
          Transaction.date >= prev_start,
          Transaction.date <= prev_end]
    if user_id:
        pq.append(Transaction.user_id == user_id)
    prev_txns = Transaction.query.filter(*pq).all()
    prev_total = sum(t.amount for t in prev_txns)
    change_pct = ((total - prev_total) / prev_total * 100) if prev_total > 0 else 0

    return {
        "total": total,
        "by_category": dict(sorted_cats),
        "by_day": dict(sorted(by_day.items())),
        "top_category": top_category,
        "avg_daily": avg_daily,
        "prev_total": prev_total,
        "change_pct": change_pct,
        "transaction_count": len(transactions)
    }


def get_financial_health_score(user_id=None):
    score = 100
    reasons = []

    wallets = Wallet.query.filter_by(user_id=user_id).all() if user_id else Wallet.query.all()
    loans = Loan.query.filter_by(loan_status='Active', user_id=user_id).all() if user_id else Loan.query.filter_by(loan_status='Active').all()
    cards = CreditCard.query.filter_by(user_id=user_id).all() if user_id else CreditCard.query.all()

    total_assets = sum(w.balance for w in wallets)
    total_debt = sum(l.outstanding_balance for l in loans)

    # Debt-to-asset ratio
    if total_assets > 0:
        dta = total_debt / total_assets
        if dta > 1.5:
            score -= 30
            reasons.append("High debt relative to assets")
        elif dta > 0.8:
            score -= 15
            reasons.append("Moderate debt load")

    # Credit utilization
    for card in cards:
        if card.credit_limit and card.credit_limit > 0:
            util = (card.credit_limit - card.available_balance) / card.credit_limit
            if util > 0.9:
                score -= 15
                reasons.append(f"{card.bank_name} near credit limit")
            elif util > 0.7:
                score -= 8

    # Emergency fund check (3 months expenses)
    today = date.today()
    three_months_ago = today - relativedelta(months=3)
    eq = [Transaction.trans_type == "expense", Transaction.date >= three_months_ago]
    if user_id:
        eq.append(Transaction.user_id == user_id)
    recent_expenses = Transaction.query.filter(*eq).all()
    monthly_avg_expense = sum(t.amount for t in recent_expenses) / 3
    if monthly_avg_expense > 0 and total_assets < (monthly_avg_expense * 3):
        score -= 20
        reasons.append("Emergency fund below 3-month threshold")

    # Overdue loans
    for loan in loans:
        if loan.next_due_date and loan.next_due_date < today:
            score -= 20
            reasons.append(f"Overdue loan: {loan.loan_name}")
            break

    score = max(0, min(100, score))

    if score >= 80:
        label, color = "Excellent", "#10b981"
    elif score >= 60:
        label, color = "Good", "#6366f1"
    elif score >= 40:
        label, color = "Fair", "#f59e0b"
    else:
        label, color = "Needs Attention", "#ef4444"

    return {"score": score, "label": label, "color": color, "reasons": reasons}