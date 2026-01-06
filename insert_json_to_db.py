import json
import os
from extract_pdf_with_gemini import save_to_database

def test_insertion():
    # Configuration
    JSON_FILE = "extracted_pdf_data.json"
    REPORT_ID = 1  # Test Report ID
    YEAR = 2024
    MONTH = 9      # September
    
    print(f"Loading data from {JSON_FILE}...")
    
    if not os.path.exists(JSON_FILE):
        print(f"Error: File {JSON_FILE} not found!")
        return

    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            extracted_data = json.load(f)
            
        print(f"Loaded {len(extracted_data)} pages of data.")
        
        print("\nStarting Database Insertion...")
        print(f"   Target DB: {os.getenv('DB_NAME', 'crimedigest')}")
        print(f"   Report ID: {REPORT_ID}")
        
        success = save_to_database(REPORT_ID, extracted_data, YEAR, MONTH)
        
        if success:
            print("\nDatabase insertion completed SUCCESSFULLY!")
        else:
            print("\nDatabase insertion FAILED.")
            print("   (Check if 'crime_heads' table is populated and DB credentials are correct in .env)")
            
    except Exception as e:
        print(f"\nAn error occurred: {str(e)}")

if __name__ == "__main__":
    test_insertion()
