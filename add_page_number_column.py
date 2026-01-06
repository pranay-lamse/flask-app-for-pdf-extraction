import mysql.connector
import os
from dotenv import load_dotenv

load_dotenv()

def add_column():
    try:
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "crimedigest")
        )
        cursor = conn.cursor()
        
        print("Adding 'page_number' column to crime_statistics table...")
        try:
            cursor.execute("ALTER TABLE crime_statistics ADD COLUMN page_number INT DEFAULT 0")
            print("Column 'page_number' added successfully.")
        except mysql.connector.Error as err:
            if err.errno == 1060:
                print("Column 'page_number' already exists.")
            else:
                print(f"Error adding column: {err}")
        
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Connection Error: {e}")

if __name__ == "__main__":
    add_column()
