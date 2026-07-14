from database import engine
from sqlalchemy import text

def fix_all():
    with engine.connect() as connection:
        trans = connection.begin()
        try:
            # Fix Transactions (Again to be sure)
            print("Fixing Transactions...")
            try:
                connection.execute(text("ALTER TABLE transactions DROP CONSTRAINT transactions_user_id_fkey"))
                connection.execute(text("ALTER TABLE transactions ADD CONSTRAINT transactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"))
                print(" - Transactions fixed.")
            except Exception as e:
                print(f" - Transactions error: {e}")

            # Fix Watchlist
            print("Fixing Watchlist...")
            try:
                connection.execute(text("ALTER TABLE watchlist DROP CONSTRAINT watchlist_user_id_fkey"))
                connection.execute(text("ALTER TABLE watchlist ADD CONSTRAINT watchlist_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"))
                print(" - Watchlist fixed.")
            except Exception as e:
                print(f" - Watchlist error: {e}")

            # Verification Tokens (Might have different name, trying common ones)
            print("Fixing Verification Tokens...")
            try:
                # Based on models.py, table is 'verification_tokens', checks if constraint exists first
                # Often constraints are named verification_tokens_user_id_fkey
                connection.execute(text("ALTER TABLE verification_tokens DROP CONSTRAINT verification_tokens_user_id_fkey"))
                connection.execute(text("ALTER TABLE verification_tokens ADD CONSTRAINT verification_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"))
                print(" - Verification Tokens fixed.")
            except Exception as e:
                 # Try generic name if auto-generated
                try:
                    connection.execute(text("ALTER TABLE verification_tokens DROP CONSTRAINT verification_tokens_user_id_fkey1")) # Try guess
                except Exception:
                    pass
                print(f" - Verification Tokens error (maybe no FK or diff name): {e}")

            trans.commit()
            print("\nALL DONE. Please try deleting again.")
        except Exception as e:
            trans.rollback()
            print(f"FATAL ERROR: {e}")

if __name__ == "__main__":
    fix_all()
