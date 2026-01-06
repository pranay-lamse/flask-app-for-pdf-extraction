import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

def check_schema():
    try:
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "crimedigest")
        )
        cursor = conn.cursor()
        
        print("\nChecking 'report_uploads' table schema:")
        cursor.execute("DESCRIBE report_uploads")
        for column in cursor.fetchall():
            print(column)
            
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_schema()
