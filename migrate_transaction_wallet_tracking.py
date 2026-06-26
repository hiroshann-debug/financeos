"""
One-time data backfill — run AFTER you've added the new wallet_id,
credit_card_id and balance_applied columns to Transaction via the
normal Flask-Migrate flow:

    flask db migrate -m "Add wallet/card tracking to Transaction"
    flask db upgrade

Then run this script once:
    python migrate_transaction_wallet_tracking.py

IMPORTANT: existing historical transactions have no way to know which
wallet/card they actually used (that information was never stored
before this fix). This script marks old transactions as
balance_applied=True (since they already affected real balances when
created) but leaves wallet_id/credit_card_id as NULL for them — this
means editing or deleting an OLD transaction still won't auto-reverse
a wallet balance (same limitation as before this fix), but it also
won't incorrectly reverse the WRONG wallet. Going forward, every NEW
transaction correctly records its wallet/card and can be safely
deleted/edited with automatic balance correction.
"""

from app import app, db
from models import Transaction


def migrate():
    with app.app_context():
        updated = Transaction.query.filter(
            (Transaction.balance_applied.is_(None)) | (Transaction.balance_applied == False)
        ).update({"balance_applied": True}, synchronize_session=False)
        db.session.commit()

        print(f"✅ Backfilled balance_applied=True on {updated} existing transaction(s).")
        print()
        print("Note: existing transactions have wallet_id=NULL since that wasn't")
        print("tracked before this fix. Deleting/editing an OLD transaction won't")
        print("auto-reverse a wallet balance — only NEW transactions created from")
        print("now on will have full automatic balance reversal on delete/edit.")


if __name__ == "__main__":
    migrate()

