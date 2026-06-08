"""Add loan_id to Transaction

Revision ID: 52945698e2e3
Revises: 
Create Date: 2025-07-03 22:52:08.807655

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '52945698e2e3'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('transaction', schema=None) as batch_op:
        batch_op.add_column(sa.Column('loan_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_transaction_loan_id', 'loan', ['loan_id'], ['id'])  # <-- name here

def downgrade():
    with op.batch_alter_table('transaction', schema=None) as batch_op:
        batch_op.drop_constraint('fk_transaction_loan_id', type_='foreignkey')  # <-- same name here
        batch_op.drop_column('loan_id')

    # ### end Alembic commands ###
