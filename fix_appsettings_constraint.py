"""Run once on Railway to drop the unique constraint on app_settings.key"""
from app import app, db

with app.app_context():
    with db.engine.connect() as conn:
        try:
            conn.execute(db.text('ALTER TABLE app_settings DROP CONSTRAINT IF EXISTS app_settings_key_key;'))
            conn.commit()
            print("✅ Unique constraint dropped")
        except Exception as e:
            print("Note:", e)
        
        # Also clean up any duplicate keys (keep most recent per user)
        try:
            conn.execute(db.text("""
                DELETE FROM app_settings a
                USING app_settings b
                WHERE a.id < b.id 
                AND a.key = b.key 
                AND a.user_id = b.user_id;
            """))
            conn.commit()
            print("✅ Duplicate settings cleaned")
        except Exception as e:
            print("Cleanup note:", e)
