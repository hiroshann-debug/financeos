from app import app, db
with app.app_context():
    db.create_all()
    with db.engine.connect() as conn:
        tables = ['transaction','wallet','fixed_expense','credit_card','loan','budget_planner','goal','recurring_payment','wallet_transfer','net_worth_history','notification','app_settings']
        for t in tables:
            try:
                conn.execute(db.text(
                    f'SELECT setval(pg_get_serial_sequence(\'{t}\',\'id\'), COALESCE((SELECT MAX(id) FROM "{t}"),1))'
                ))
            except: pass
        conn.commit()
        print('Done!')