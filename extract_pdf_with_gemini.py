import os
import json
import base64
from pdf2image import convert_from_path
import requests
from pathlib import Path
from dotenv import load_dotenv
import mysql.connector
from datetime import datetime

# Load environment variables
load_dotenv()

# Configure Gemini API
API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyDmruExS4O2OqNr_yBJXBzaUDv0pPDD1Cc")
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={API_KEY}"

def get_db_connection():
    """Establish and return a database connection"""
    try:
        return mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "crimedigest")
        )
    except mysql.connector.Error as err:
        print(f"Error connecting to database: {err}")
        return None

def get_or_create_crime_head_id(cursor, head_name):
    """
    Look up crime head ID by name.
    If not found, CREATE it automatically.
    """
    if not head_name:
        return None
        
    head_name = head_name.strip()
    
    # 1. Try finding it (Exact match first)
    cursor.execute("SELECT id FROM crime_heads WHERE name = %s", (head_name,))
    result = cursor.fetchone()
    if result:
        return result['id']
        
    # 2. Try Fuzzy match (Fallback for minor typos, but strict enough to avoid wrong merges)
    # Removing 'LIKE' for now to ensure we respect the hierarchy extracted by Gemini (e.g. "Dacoity - Prof.")
    # vs just matching any "Prof.". Strict creation is better for data integrity here.
    
    # 3. If not found, create it
    try:
        print(f"  - Creating new Crime Head: '{head_name}'")
        # Default category to 'Uncategorized' or similar
        cursor.execute("INSERT INTO crime_heads (name, category) VALUES (%s, %s)", (head_name, 'Other'))
        return cursor.lastrowid
    except mysql.connector.Error as err:
        # Handle race condition (duplicate entry)
        if err.errno == 1062: # Duplicate entry
            cursor.execute("SELECT id FROM crime_heads WHERE name = %s", (head_name,))
            result = cursor.fetchone()
            if result:
                return result['id']
        
        print(f"  - Error creating crime head '{head_name}': {err}")
        return None

def save_to_database(report_id, extracted_data, year=None, month=None):
    """
    Save extracted data to MySQL database with transaction support
    Mimics the PHP logic provided by user
    """
    conn = get_db_connection()
    if not conn:
        print("Skipping database insertion: specific database connection required")
        return False
        
    cursor = conn.cursor(dictionary=True, buffered=True)
    
    try:
        # Start transaction
        conn.start_transaction()
        print(f"Started DB Transaction for Report ID: {report_id}")

        # 1. Delete existing records for this report
        tables_to_clear = ['crime_statistics', 'pending_cases_by_head', 'conviction_stats']
        for table in tables_to_clear:
            try:
                cursor.execute(f"DELETE FROM {table} WHERE report_upload_id = %s", (report_id,))
                print(f"  - Cleared old records from {table}")
            except mysql.connector.Error as err:
                 print(f"  - Warning: Could not clear {table}: {err}")

        processed_items = 0
        
        # Determine period string
        period = "Full Year"
        if month:
            # simple mapping, PHP used mktime for month name
            months = ["", "January", "February", "March", "April", "May", "June", 
                      "July", "August", "September", "October", "November", "December"]
            if 1 <= int(month) <= 12:
                period = months[int(month)]

        # Iterate through pages and extracted items
        for page_data in extracted_data:
            stats_list = page_data.get('crime_statistics', [])
            if not isinstance(stats_list, list):
                # Handle case where it might be a dictionary or nested differently
                # depending on exact JSON structure returned by Gemini
                if 'rows' in page_data: # Common structure
                    stats_list = page_data['rows']
                elif 'data' in page_data:
                    stats_list = page_data['data']
                else: 
                     # If extraction returned a flat list in 'data' key mainly
                     stats_list = [page_data] if isinstance(page_data, dict) else []

            for stat in stats_list:
                # Validate required fields
                if not stat.get('crime_head'):
                    continue
                
                # Get or Create Crime Head ID
                head_id = get_or_create_crime_head_id(cursor, stat['crime_head'])
                if not head_id:
                    print(f"  - Warning: Could not create crime head '{stat['crime_head']}'")
                    continue

                # Parse values (handle strings/ints/none)
                registered = int(stat.get('registered', 0))
                detected = int(stat.get('detected', 0))
                
                # Calculate percent
                det_percent = round((detected / registered) * 100, 2) if registered > 0 else 0

                # 2. Insert into CrimeStatistic
                sql_crime = """
                    INSERT INTO crime_statistics 
                    (report_upload_id, crime_head_id, year, period, registered, detected, detection_percent)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                cursor.execute(sql_crime, (report_id, head_id, year, period, registered, detected, det_percent))

                # 3. Insert into pending_cases_by_head
                sql_pending = """
                    INSERT INTO pending_cases_by_head
                    (report_upload_id, crime_head_id, month_0_3, month_3_6, month_6_12, above_1_year)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """
                cursor.execute(sql_pending, (
                    report_id, 
                    head_id, 
                    int(stat.get('pending_0_3', 0)),
                    int(stat.get('pending_3_6', 0)),
                    int(stat.get('pending_6_12', 0)),
                    int(stat.get('pending_1_year', 0))
                ))
                
                processed_items += 1

            # 4. Conviction Stats (usually one per report/page section)
            conviction = page_data.get('conviction_stats', {})
            if conviction:
                decided = int(conviction.get('decided', 0))
                convicted = int(conviction.get('convicted', 0))
                acquitted = int(conviction.get('acquitted', 0))
                
                # Auto-calculate decided if missing
                if decided == 0 and (convicted + acquitted) > 0:
                    decided = convicted + acquitted
                
                conv_percent = round((convicted / decided) * 100, 2) if decided > 0 else 0
                
                sql_conv = """
                    INSERT INTO conviction_stats
                    (report_upload_id, year, decided, convicted, acquitted, conviction_percent)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """
                cursor.execute(sql_conv, (report_id, year, decided, convicted, acquitted, conv_percent))
                print("  - Inserted Conviction Stats")

        # 5. Commit Transaction
        conn.commit()
        print(f"DB Transaction Committed. Processed {processed_items} items.")
        
        # Update Log (simulated)
        # Handle list or dict/single item
        stats_list = page_data.get('crime_statistics', [])
        # Normalization (similar to previous logic)
        if not isinstance(stats_list, list):
             if 'rows' in page_data: stats_list = page_data['rows']
             elif 'data' in page_data: stats_list = page_data['data']
             else: stats_list = [page_data] if isinstance(page_data, dict) else []

        for stat in stats_list:
            if not stat.get('crime_head'): continue
            
            # Get/Create Head ID
            head_id = get_or_create_crime_head_id(cursor, stat['crime_head'])
            if not head_id: continue

            # Values
            registered = int(stat.get('registered', 0))
            detected = int(stat.get('detected', 0))
            det_percent = round((detected / registered) * 100, 2) if registered > 0 else 0
            
            # Insert Crime Stats
            cursor.execute("""
                INSERT INTO crime_statistics 
                (report_upload_id, crime_head_id, year, period, registered, detected, detection_percentage, page_number)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (report_id, head_id, year, period, registered, detected, det_percent, page_data.get('page_number', 0)))

            # Insert Pending Cases (New Table)
            cursor.execute("""
                INSERT INTO pending_cases_by_head
                (report_upload_id, crime_head_id, month_0_3, month_3_6, month_6_12, above_1_year)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (report_id, head_id, int(stat.get('pending_0_3', 0)), int(stat.get('pending_3_6', 0)), 
                  int(stat.get('pending_6_12', 0)), int(stat.get('pending_1_year', 0))))

        # Insert Conviction Stats
        conviction = page_data.get('conviction_stats', {})
        if conviction:
            decided = int(conviction.get('decided', 0))
            convicted = int(conviction.get('convicted', 0))
            acquitted = int(conviction.get('acquitted', 0))
            if decided == 0 and (convicted + acquitted) > 0: decided = convicted + acquitted
            conv_percent = round((convicted / decided) * 100, 2) if decided > 0 else 0
            
            cursor.execute("""
                INSERT INTO conviction_stats
                (report_upload_id, year, decided, convicted, acquitted, conviction_percent)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (report_id, year, decided, convicted, acquitted, conv_percent))

        conn.commit()
        cursor.close()
        conn.close()
        return True
                (report_upload_id, year, decided, convicted, acquitted, conviction_percent, page_number)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql_conv, (report_id, year, decided, convicted, acquitted, conv_percent, page_number))
            print(f"  - Inserted Conviction Stats for page {page_number}")

        conn.commit()
        print(f"  - Page {page_number} data committed. Processed {processed_items} items.")
        return True

    except Exception as e:
        print(f"Error saving page {page_number}: {str(e)}")
        conn.rollback()
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

def clear_report_data(report_id):
    """
    Deletes all existing records for a given report_upload_id from relevant tables.
    """
    conn = get_db_connection()
    if not conn:
        print("Skipping data clear: specific database connection required")
        return False
    
    cursor = conn.cursor()
    try:
        conn.start_transaction()
        tables_to_clear = ['crime_statistics', 'pending_cases_by_head', 'conviction_stats']
        for table in tables_to_clear:
            try:
                cursor.execute(f"DELETE FROM {table} WHERE report_upload_id = %s", (report_id,))
                print(f"  - Cleared old records from {table} for report {report_id}")
            except mysql.connector.Error as err:
                 print(f"  - Warning: Could not clear {table} for report {report_id}: {err}")
        conn.commit()
        return True
    except Exception as e:
        print(f"Error clearing report data for {report_id}: {str(e)}")
        conn.rollback()
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

def get_last_processed_page(report_id):
    """
    Get the last page number that was successfully inserted into the DB for this report.
    Returns 0 if no pages processed.
    """
    conn = get_db_connection()
    if not conn: return 0
    
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(page_number) FROM crime_statistics WHERE report_upload_id = %s", (report_id,))
        result = cursor.fetchone()
        last_page = result[0] if result and result[0] else 0
        return last_page
    except Exception as e:
        print(f"Error checking progress: {e}")
        return 0
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

def pdf_to_images(pdf_path, output_folder="temp_images"):
    """
    Convert PDF pages to images
    
    Args:
        pdf_path: Path to the PDF file
        output_folder: Folder to save temporary images
        
    Returns:
        List of image paths
    """
    # Create output folder if it doesn't exist
    os.makedirs(output_folder, exist_ok=True)
    
    print(f"Converting PDF to images: {pdf_path}")
    
    # Convert PDF to images
    images = convert_from_path(pdf_path, dpi=300)
    
    image_paths = []
    for i, image in enumerate(images):
        image_path = os.path.join(output_folder, f"page_{i+1}.png")
        image.save(image_path, "PNG")
        image_paths.append(image_path)
        print(f"  Saved page {i+1} to {image_path}")
    
    return image_paths

def encode_image_to_base64(image_path):
    """
    Encode image to base64 string
    
    Args:
        image_path: Path to the image file
        
    Returns:
        Base64 encoded string
    """
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def extract_pdf_content_with_gemini(image_paths, report_id=None, year=2024, month=None, prompt=None):
    """
    Extract content from PDF images and optionally save to DB incrementally.
    Supports RESUME capability by checking last processed page.
    """
    # Default prompt optimized for SQL Schema matching
    if prompt is None:
        prompt = """
        You are an expert data extractor. analyze the crime statistics table in these images.
        
        Extract the rows into a JSON list.
        Review the table structure carefully. It often contains main headers and sub-headers (indented).
        
        IMPORTANT: For sub-headers (like 'Prof.', 'Technical.', 'Major', 'Minor'), you MUST combine them with their PARENT header to create a unique 'crime_head'.
        Example Structure:
        - Rape
          - Minor
          - Major
        - Dacoity
          - Prof.
          - Technical.
          
        Extraction Output Should Be:
        [
          {"crime_head": "Rape", ...},
          {"crime_head": "Rape - Minor", ...},
          {"crime_head": "Rape - Major", ...},
          {"crime_head": "Dacoity", ...},
          {"crime_head": "Dacoity - Prof.", ...},
          {"crime_head": "Dacoity - Technical.", ...}
        ]
        
        If a row looks like a total or sub-total, include it.
        
        For each row, extract:
        1. "crime_head": The full hierarchical name as described above (String).
        2. "registered": The number of registered cases (Int). Look for columns like "Reg.", "Registered", or "Cases".
        3. "detected": The number of detected cases (Int). Look for columns like "Det.", "Detected".
        4. "pending_0_3": Pending cases 0-3 months (Int).
        5. "pending_3_6": Pending cases 3-6 months (Int).
        6. "pending_6_12": Pending cases 6-12 months (Int).
        7. "pending_1_year": Pending cases > 1 year (Int).
        
        Also look for a "Conviction" table or section and extract:
        - "conviction_stats": {
            "decided": Int,
            "convicted": Int,
            "acquitted": Int
        }
        
        Return ONLY valid JSON.
        """
    
    all_results = []
    
    # RESUME LOGIC
    start_index = 0
    if report_id:
        last_page = get_last_processed_page(report_id)
        if last_page > 0:
            print(f"🔄 RESUMING extraction from Page {last_page + 1} (Pages 1-{last_page} already in DB)")
            start_index = last_page
            # Don't clear data if resuming!
        else:
            # First time for this report, clear any partial junk
            clear_report_data(report_id)
    
    for idx, image_path in enumerate(image_paths):
        current_page_num = idx + 1
        
        # SKIP pages already processed
        if current_page_num <= start_index:
            print(f"  - Skipping page {current_page_num} (already processed)")
            continue
            
        print(f"\nProcessing page {current_page_num}/{len(image_paths)}: {image_path}")
        
        base64_image = encode_image_to_base64(image_path)
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64_image
                        }
                    }
                ]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json"
            }
        }
        
        try:
            # Make the API request
            response = requests.post(
                GEMINI_API_URL,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=60
            )
            
            # Check if request was successful
            if response.status_code == 200:
                response_data = response.json()
                
                # Extract the text content from the response
                if 'candidates' in response_data and len(response_data['candidates']) > 0:
                    content = response_data['candidates'][0]['content']['parts'][0]['text']
                    
                    try:
                        result = json.loads(content)
                        # Ensure strict structure
                        if isinstance(result, list):
                            result = {"crime_statistics": result}
                        elif "rows" in result:
                            result["crime_statistics"] = result["rows"]

                        result['page_number'] = current_page_num
                        all_results.append(result)
                        print(f"  [SUCCESS] Successfully extracted data from page {current_page_num}")

                        # Incremental Save
                        if report_id:
                            print(f"  -> Saving Page {current_page_num} to Database...")
                            save_page_to_database(report_id, result, year, month)

                    except json.JSONDecodeError:
                        print(f"  [ERROR] Failed to parse JSON from page {current_page_num}")
                        all_results.append({
                            'page_number': current_page_num,
                            'raw_response': content,
                            'error': 'JSON parsing failed'
                        })
                else:
                    print(f"  [ERROR] No valid response from API for page {current_page_num}")
                    all_results.append({
                        'page_number': current_page_num,
                        'error': 'No valid response from API',
                        'raw_response': response_data
                    })
            else:
                error_msg = response.json() if response.text else {"error": "Unknown error"}
                print(f"  [ERROR] API request failed with status {response.status_code}")
                print(f"  Error details: {error_msg}")
                all_results.append({
                    'page_number': current_page_num,
                    'error': f'API request failed: {response.status_code}',
                    'error_details': error_msg
                })
                
        except Exception as e:
            print(f"  [ERROR] Error processing page {current_page_num}: {str(e)}")
            all_results.append({
                'page_number': current_page_num,
                'error': str(e)
            })
    
    return all_results

def cleanup_temp_images(image_folder="temp_images"):
    """
    Remove temporary image files
    
    Args:
        image_folder: Folder containing temporary images
    """
    if os.path.exists(image_folder):
        for file in os.listdir(image_folder):
            os.remove(os.path.join(image_folder, file))
        os.rmdir(image_folder)
        print(f"\nCleaned up temporary images from {image_folder}")

def main():
    # PDF file path
    pdf_path = "bhandara sept.pdf"
    
    # Mock parameters (In a real app, these would come from CLI args or API request)
    REPORT_ID = 1  # Replace with actual report_upload_id
    YEAR = 2024
    MONTH = 9      # September
    
    if not os.path.exists(pdf_path):
        print(f"Error: PDF file not found: {pdf_path}")
        return
    
    print("="*60)
    print("PDF Content Extraction with Gemini Flash + DB Insert")
    print("="*60)
    
    # Step 1: Convert PDF to images
    image_paths = pdf_to_images(pdf_path)
    print(f"\n[OK] Converted {len(image_paths)} pages to images")
    
    # Step 2: Extract content using Gemini
    print("\n" + "="*60)
    print("Extracting content with Gemini Flash API...")
    print("="*60)
    
    results = extract_pdf_content_with_gemini(image_paths)
    
    # Step 3: Save results to JSON file
    output_file = "extracted_pdf_data.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print("\n" + "="*60)
    print(f"[SUCCESS] Extraction complete! Results saved to: {output_file}")
    print("="*60)
    
    # Step 4: Save to Database
    print("\n" + "="*60)
    print("Inserting data into Database...")
    print("="*60)
    
    db_success = save_to_database(REPORT_ID, results, YEAR, MONTH)
    
    if db_success:
        print("\n✅ Database sync completed successfully!")
    else:
        print("\n⚠️ Database sync skipped or failed (check connection/credentials).")
    
    # Step 5: Cleanup temporary images
    cleanup_temp_images()
    
    print(f"\nYou can now view the extracted data in: {output_file}")

if __name__ == "__main__":
    main()
