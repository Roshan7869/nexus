import sqlite3
import os

def migrate():
    db_path = "db/nexus.db"
    sql_path = "db/migrations/001_constraints.sql"
    
    if not os.path.exists(db_path):
        print(f"Error: {db_path} does not exist.")
        return
        
    conn = sqlite3.connect(db_path)
    try:
        with open(sql_path, "r") as f:
            sql = f.read()
        conn.executescript(sql)
        conn.commit()
        print("Migration applied successfully.")
    except Exception as e:
        print(f"Error during migration: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
