import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

def setup_database():
    try:
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "crimedigest")
        )
        cursor = conn.cursor()
        
        # Read SQL file
        with open("create_pending_cases_table.sql", "r") as f:
            sql = f.read()
            
        print("Creating table 'pending_cases_by_head'...")
        cursor.execute(sql)
        conn.commit()
        
        print("Table created successfully!")
        
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    setup_database()
