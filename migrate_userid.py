
from app import app, db
with app.app_context():
    with db.engine.connect() as conn:
        try:
            conn.execute(db.text('ALTER TABLE loan ADD COLUMN wallet_id INTEGER'))
            conn.commit()
            print('Done!')
        except: print('Already exists - OK')