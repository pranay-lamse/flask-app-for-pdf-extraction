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
    # 1. Try finding it
    cursor.execute("SELECT id FROM crime_heads WHERE name = %s OR name LIKE %s LIMIT 1", (head_name, f"%{head_name}%"))
    result = cursor.fetchone()
    if result:
        return result['id']
    
    # 2. If not found, create it
    try:
        print(f"  - Creating new Crime Head: '{head_name}'")
        # Default category to 'Uncategorized' or similar
        cursor.execute("INSERT INTO crime_heads (name, category) VALUES (%s, %s)", (head_name, 'Other'))
        return cursor.lastrowid
    except mysql.connector.Error as err:
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
        if hasattr(cursor, 'execute'):
             # If you have a logs table you'd update it here
             pass
             
        cursor.close()
        conn.close()
        return True

    except Exception as e:
        import traceback
        conn.rollback()
        print(f"DB Transaction Rolled Back. Error: {str(e)}")
        print(traceback.format_exc())
        if conn and conn.is_connected():
            cursor.close()
            conn.close()
        return False

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

def extract_pdf_content_with_gemini(image_paths, prompt=None):
    """
    Extract content from PDF images using Gemini Flash API via REST
    
    Args:
        image_paths: List of image file paths
        prompt: Custom prompt for extraction (optional)
        
    Returns:
        JSON response from Gemini
    """
    # Default prompt optimized for SQL Schema matching
    if prompt is None:
        prompt = """
        Analyze this PDF page (Police Crime Statistics) and extract data for database insertion.
        
        REQUIRED JSON STRUCTURE:
        {
          "crime_statistics": [
            {
              "crime_head": "Name of crime (e.g., Murder, Rape)",
              "registered": 10,
              "detected": 8,
              "pending_0_3": 1,
              "pending_3_6": 0,
              "pending_6_12": 2,
              "pending_1_year": 5
            }
          ],
          "conviction_stats": {
            "decided": 100,
            "convicted": 60,
            "acquitted": 40
          },
          "metadata": {
            "year": 2024,
            "month": 9
          }
        }
        
        IMPORTANT:
        - "crime_head" must match exact official names if possible.
        - Ensure numeric values are integers (0 if missing).
        - Return ONLY valid JSON.
        """
    
    all_results = []
    
    for idx, image_path in enumerate(image_paths):
        print(f"\nProcessing page {idx + 1}/{len(image_paths)}: {image_path}")
        
        try:
            # Encode image to base64
            image_base64 = encode_image_to_base64(image_path)
            
            # Create the request payload
            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": image_base64
                            }
                        }
                    ]
                }],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json"
                }
            }
            
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
                        result['page_number'] = idx + 1
                        all_results.append(result)
                        print(f"  [SUCCESS] Successfully extracted data from page {idx + 1}")
                    except json.JSONDecodeError:
                        print(f"  [ERROR] Failed to parse JSON from page {idx + 1}")
                        all_results.append({
                            'page_number': idx + 1,
                            'raw_response': content,
                            'error': 'JSON parsing failed'
                        })
                else:
                    print(f"  [ERROR] No valid response from API for page {idx + 1}")
                    all_results.append({
                        'page_number': idx + 1,
                        'error': 'No valid response from API',
                        'raw_response': response_data
                    })
            else:
                error_msg = response.json() if response.text else {"error": "Unknown error"}
                print(f"  [ERROR] API request failed with status {response.status_code}")
                print(f"  Error details: {error_msg}")
                all_results.append({
                    'page_number': idx + 1,
                    'error': f'API request failed: {response.status_code}',
                    'error_details': error_msg
                })
                
        except Exception as e:
            print(f"  [ERROR] Error processing page {idx + 1}: {str(e)}")
            all_results.append({
                'page_number': idx + 1,
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
    pdf_path = "bhandara sept-2-3.pdf"
    
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
