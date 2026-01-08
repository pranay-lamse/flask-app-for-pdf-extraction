import os
import json
import base64
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from pdf2image import convert_from_path
import requests
from werkzeug.utils import secure_filename
from datetime import datetime
from dotenv import load_dotenv
import mysql.connector

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}

# Create uploads folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Get API key from environment variable
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

if GEMINI_API_KEY:
    print(f"API Key loaded: {GEMINI_API_KEY[:5]}...{GEMINI_API_KEY[-4:]}")
else:
    print("Warning: GEMINI_API_KEY not found in .env! Check .env file.")

GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def pdf_to_images(pdf_path, output_folder):
    """Convert PDF pages to images"""
    images = convert_from_path(pdf_path, dpi=300)
    image_paths = []
    
    for i, image in enumerate(images):
        image_path = os.path.join(output_folder, f"page_{i+1}.png")
        image.save(image_path, "PNG")
        image_paths.append(image_path)
    
    return image_paths

def encode_image_to_base64(image_path):
    """Encode image to base64 string"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# MySQL Configuration
def get_db_connection():
    try:
        return mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "crimedigest")
        )
    except Exception as e:
        print(f"DB Connection Error: {e}")
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
    
    # 2. If not found, create it
    try:
        print(f"  - Creating new Crime Head: '{head_name}'")
        cursor.execute("INSERT INTO crime_heads (name, category) VALUES (%s, %s)", (head_name, 'Other'))
        return cursor.lastrowid
    except mysql.connector.Error as err:
        # Handle race condition (duplicate entry)
        if err.errno == 1062:  # Duplicate entry
            cursor.execute("SELECT id FROM crime_heads WHERE name = %s", (head_name,))
            result = cursor.fetchone()
            if result:
                return result['id']
        
        print(f"  - Error creating crime head '{head_name}': {err}")
        return None

def save_page_to_database(report_id, page_data, year=None, month=None):
    """
    Save a single page of extracted data to database incrementally
    """
    conn = get_db_connection()
    if not conn:
        print("Skipping database insertion: database connection failed")
        return False
        
    cursor = conn.cursor(dictionary=True, buffered=True)
    
    try:
        conn.start_transaction()
        page_number = page_data.get('page_number', 0)
        
        # Determine period string
        period = "Full Year"
        if month:
            months = ["", "January", "February", "March", "April", "May", "June", 
                      "July", "August", "September", "October", "November", "December"]
            if 1 <= int(month) <= 12:
                period = months[int(month)]
        
        # Extract crime statistics
        stats_list = page_data.get('crime_statistics', [])
        if not isinstance(stats_list, list):
            if 'rows' in page_data:
                stats_list = page_data['rows']
            elif 'data' in page_data:
                stats_list = page_data['data']
            else:
                stats_list = [page_data] if isinstance(page_data, dict) else []
        
        processed_items = 0
        for stat in stats_list:
            if not stat.get('crime_head'):
                continue
            
            # Get or Create Crime Head ID
            head_id = get_or_create_crime_head_id(cursor, stat['crime_head'])
            if not head_id:
                continue
            
            # Parse values
            registered = int(stat.get('registered', 0))
            detected = int(stat.get('detected', 0))
            det_percent = round((detected / registered) * 100, 2) if registered > 0 else 0
            
            # Insert Crime Stats
            cursor.execute("""
                INSERT INTO crime_statistics 
                (report_upload_id, crime_head_id, year, period, registered, detected, detection_percent, page_number)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (report_id, head_id, year, period, registered, detected, det_percent, page_number))
            
            # Insert Pending Cases
            cursor.execute("""
                INSERT INTO pending_cases_by_head
                (report_upload_id, crime_head_id, month_0_3, month_3_6, month_6_12, above_1_year)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (report_id, head_id, int(stat.get('pending_0_3', 0)), int(stat.get('pending_3_6', 0)), 
                  int(stat.get('pending_6_12', 0)), int(stat.get('pending_1_year', 0))))
            
            processed_items += 1
        
        # Insert Conviction Stats (usually one per page)
        conviction = page_data.get('conviction_stats', {})
        if conviction:
            decided = int(conviction.get('decided', 0))
            convicted = int(conviction.get('convicted', 0))
            acquitted = int(conviction.get('acquitted', 0))
            
            if decided == 0 and (convicted + acquitted) > 0:
                decided = convicted + acquitted
            
            conv_percent = round((convicted / decided) * 100, 2) if decided > 0 else 0
            
            cursor.execute("""
                INSERT INTO conviction_stats
                (report_upload_id, year, decided, convicted, acquitted, conviction_percent, page_number)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (report_id, year, decided, convicted, acquitted, conv_percent, page_number))
        
        conn.commit()
        print(f"  - Page {page_number} saved to DB. Processed {processed_items} items.")
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
                print(f"  - Warning: Could not clear {table}: {err}")
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
    if not conn:
        return 0
    
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

def extract_pdf_content_with_gemini(image_paths, custom_prompt=None, report_id=None, year=None, month=None, save_to_db=False):
    """Extract content from PDF images using Gemini API with optional DB saving"""
    # Crime statistics extraction prompt (optimized for the specific use case)
    crime_stats_prompt = """
    You are an expert data extractor. Analyze the crime statistics table in this image.
    
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
    
    default_general_prompt = """
    Analyze this PDF page and extract all the information in a structured JSON format.
    
    Please provide:
    1. Title/Header information
    2. Tables (if any) with all rows and columns
    3. Text content organized by sections
    4. Any numerical data or statistics
    5. Dates, locations, or other metadata
    
    Return the response as valid JSON only, no additional text.
    """
    
    # Use custom prompt if provided, otherwise use crime stats prompt for DB saving, else general
    if custom_prompt:
        prompt = custom_prompt
    elif save_to_db:
        prompt = crime_stats_prompt
    else:
        prompt = default_general_prompt
    
    all_results = []
    
    # RESUME LOGIC for database saving
    start_index = 0
    if save_to_db and report_id:
        last_page = get_last_processed_page(report_id)
        if last_page > 0:
            print(f"🔄 RESUMING extraction from Page {last_page + 1} (Pages 1-{last_page} already in DB)")
            start_index = last_page
        else:
            # First time for this report, clear any partial data
            clear_report_data(report_id)
    
    for idx, image_path in enumerate(image_paths):
        current_page_num = idx + 1
        
        # SKIP pages already processed (for resume capability)
        if current_page_num <= start_index:
            print(f"  - Skipping page {current_page_num} (already processed)")
            continue
        
        try:
            image_base64 = encode_image_to_base64(image_path)
            
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
            
            response = requests.post(
                GEMINI_API_URL,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=60
            )
            
            if response.status_code == 200:
                response_data = response.json()
                
                if 'candidates' in response_data and len(response_data['candidates']) > 0:
                    content = response_data['candidates'][0]['content']['parts'][0]['text']
                    
                    try:
                        result = json.loads(content)
                        # Ensure strict structure for crime stats
                        if save_to_db and isinstance(result, list):
                            result = {"crime_statistics": result}
                        elif save_to_db and "rows" in result:
                            result["crime_statistics"] = result["rows"]
                        
                        result['page_number'] = current_page_num
                        all_results.append(result)
                        print(f"  [SUCCESS] Page {current_page_num} extracted")
                        
                        # Incremental Save to Database
                        if save_to_db and report_id:
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
                    all_results.append({
                        'page_number': current_page_num,
                        'error': 'No valid response from API',
                        'raw_response': response_data
                    })
            else:
                error_msg = response.json() if response.text else {"error": "Unknown error"}
                all_results.append({
                    'page_number': current_page_num,
                    'error': f'API request failed: {response.status_code}',
                    'error_details': error_msg
                })
                
        except Exception as e:
            all_results.append({
                'page_number': current_page_num,
                'error': str(e)
            })
    
    return all_results

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/upload')
def upload_page():
    return render_template('index.html')

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for Railway"""
    api_key_set = bool(GEMINI_API_KEY)
    return jsonify({
        'status': 'healthy',
        'gemini_api_configured': api_key_set,
        'model': GEMINI_MODEL
    })

@app.route('/list-pdfs', methods=['GET'])
def list_pdfs():
    """
    List all uploaded PDF files
    
    Response:
        JSON array with PDF file information
    """
    try:
        pdfs = []
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            if filename.endswith('.pdf'):
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file_stats = os.stat(filepath)
                pdfs.append({
                    'name': filename,
                    'size': file_stats.st_size,
                    'modified': datetime.fromtimestamp(file_stats.st_mtime).isoformat()
                })
        
        # Sort by modified time, newest first
        pdfs.sort(key=lambda x: x['modified'], reverse=True)
        
        return jsonify({
            'success': True,
            'count': len(pdfs),
            'pdfs': pdfs
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/extract-saved/<filename>', methods=['POST'])
def extract_saved_pdf(filename):
    """
    Extract content from a previously uploaded PDF
    
    Args:
        filename: Name of the PDF file in uploads folder
    
    Response:
        JSON with extracted data
    """
    # Check if API key is configured
    if not GEMINI_API_KEY:
        return jsonify({
            'error': 'Gemini API key not configured. Please set GEMINI_API_KEY environment variable.'
        }), 500
    
    # Sanitize filename
    filename = secure_filename(filename)
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    # Check if file exists
    if not os.path.exists(pdf_path):
        return jsonify({'error': 'PDF file not found'}), 404
    
    try:
        # Create temporary directory for processing
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            # Convert PDF to images
            image_folder = os.path.join(temp_dir, 'images')
            os.makedirs(image_folder, exist_ok=True)
            image_paths = pdf_to_images(pdf_path, image_folder)
            
            # Extract content using Gemini
            results = extract_pdf_content_with_gemini(image_paths, None)
            
            # Return results
            return jsonify({
                'success': True,
                'filename': filename,
                'total_pages': len(results),
                'data': results
            })
            
    except Exception as e:
        return jsonify({
            'error': f'Processing failed: {str(e)}'
        }), 500

@app.route('/extract', methods=['POST'])
def extract_pdf():
    """
    Extract content from uploaded PDF
    
    Request:
        - file: PDF file (multipart/form-data)
        - prompt: Optional custom extraction prompt (optional)
    
    Response:
        JSON array with extracted data from each page
    """
    # Check if API key is configured
    if not GEMINI_API_KEY:
        return jsonify({
            'error': 'Gemini API key not configured. Please set GEMINI_API_KEY environment variable.'
        }), 500
    
    # Check if file is present in request
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    # Check if filename is empty
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Check file extension
    if not allowed_file(file.filename):
        return jsonify({'error': 'Only PDF files are allowed'}), 400
    
    # Get optional custom prompt
    custom_prompt = request.form.get('prompt', None)
    
    try:
        # Save uploaded PDF permanently
        filename = secure_filename(file.filename)
        
        # Add timestamp if file already exists
        if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
            name, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{name}_{timestamp}{ext}"
        
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(pdf_path)
        
        # Create temporary directory for image processing
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            # Convert PDF to images
            image_folder = os.path.join(temp_dir, 'images')
            os.makedirs(image_folder, exist_ok=True)
            image_paths = pdf_to_images(pdf_path, image_folder)
            
            # Extract content using Gemini
            results = extract_pdf_content_with_gemini(image_paths, custom_prompt)
            
            # Return results
            return jsonify({
                'success': True,
                'filename': filename,
                'total_pages': len(results),
                'data': results
            })
            
    except Exception as e:
        return jsonify({
            'error': f'Processing failed: {str(e)}'
        }), 500

@app.route('/api/extract-and-save', methods=['POST'])
def extract_and_save_pdf():
    """
    Extract content from uploaded PDF AND save to database
    Supports resume capability for large PDFs
    
    Request (multipart/form-data):
        - file: PDF file
        - report_id: Report upload ID (integer)
        - year: Year (integer, optional)
        - month: Month (1-12, optional)
        - prompt: Optional custom extraction prompt
    
    Response:
        JSON with extraction results and database status
    """
    # Check if API key is configured
    if not GEMINI_API_KEY:
        return jsonify({
            'error': 'Gemini API key not configured. Please set GEMINI_API_KEY environment variable.'
        }), 500
    
    # Check if file is present in request
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    # Check if filename is empty
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Check file extension
    if not allowed_file(file.filename):
        return jsonify({'error': 'Only PDF files are allowed'}), 400
    
    # Get parameters
    try:
        report_id = int(request.form.get('report_id', 0))
        if report_id <= 0:
            return jsonify({'error': 'Valid report_id is required'}), 400
        
        year = int(request.form.get('year')) if request.form.get('year') else None
        month = int(request.form.get('month')) if request.form.get('month') else None
        custom_prompt = request.form.get('prompt', None)
        
    except ValueError:
        return jsonify({'error': 'Invalid year, month, or report_id format'}), 400
    
    try:
        # Save uploaded PDF permanently
        filename = secure_filename(file.filename)
        
        # Add timestamp if file already exists
        if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
            name, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{name}_{timestamp}{ext}"
        
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(pdf_path)
        
        # Create temporary directory for image processing
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            # Convert PDF to images
            image_folder = os.path.join(temp_dir, 'images')
            os.makedirs(image_folder, exist_ok=True)
            image_paths = pdf_to_images(pdf_path, image_folder)
            
            # Extract content using Gemini with database saving
            results = extract_pdf_content_with_gemini(
                image_paths, 
                custom_prompt=custom_prompt,
                report_id=report_id,
                year=year,
                month=month,
                save_to_db=True
            )
            
            # Return results
            return jsonify({
                'success': True,
                'filename': filename,
                'report_id': report_id,
                'total_pages': len(results),
                'data': results,
                'database_saved': True
            })
            
    except Exception as e:
        return jsonify({
            'error': f'Processing failed: {str(e)}'
        }), 500

@app.route('/api/latest-report-data')
def get_latest_report_data():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    try:
        cursor = conn.cursor(dictionary=True)
        
        # 1. Get Latest Report ID (from metadata or stats if metadata table empty)
        # Try fetching from report_uploads first
        cursor.execute("SELECT id, year, month, created_at FROM report_uploads ORDER BY id DESC LIMIT 1")
        report = cursor.fetchone()
        
        if not report:
            # Fallback: Check crime_statistics if report_uploads is empty (testing scenario)
            cursor.execute("SELECT DISTINCT report_upload_id as id FROM crime_statistics ORDER BY report_upload_id DESC LIMIT 1")
            report = cursor.fetchone()
            
        if not report:
            return jsonify({'error': 'No reports found in database'}), 404
            
        report_id = report['id']
        
        # 2. Fetch Crime Stats (Join with Crime Heads)
        cursor.execute("""
            SELECT 
                cs.registered, cs.detected, cs.detection_percent, 
                ch.name as crime_head
            FROM crime_statistics cs
            JOIN crime_heads ch ON cs.crime_head_id = ch.id
            WHERE cs.report_upload_id = %s
        """, (report_id,))
        crime_stats = cursor.fetchall()
        
        # 3. Fetch Pending Cases (Join with Crime Heads)
        cursor.execute("""
            SELECT 
                pc.month_0_3 as pending_0_3, pc.month_3_6 as pending_3_6, 
                pc.month_6_12 as pending_6_12, pc.above_1_year as pending_1_year,
                ch.name as crime_head
            FROM pending_cases_by_head pc
            JOIN crime_heads ch ON pc.crime_head_id = ch.id
            WHERE pc.report_upload_id = %s
        """, (report_id,))
        pending_stats = cursor.fetchall()
        
        # 4. Fetch Conviction Stats
        cursor.execute("""
            SELECT decided, convicted, acquitted 
            FROM conviction_stats 
            WHERE report_upload_id = %s 
            ORDER BY id DESC LIMIT 1
        """, (report_id,))
        conviction_stats = cursor.fetchone()
        
        return jsonify({
            'report_id': report_id,
            'year': report.get('year'),
            'month': report.get('month'),
            'crime_statistics': crime_stats,
            'rows': crime_stats,  # For frontend compatibility
            'pending_by_head': pending_stats,
            'conviction_stats': conviction_stats
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Debug mode enables auto-reload
    print(f"Starting Flask server in DEBUG mode...")
    print(f"API Key configured: {bool(GEMINI_API_KEY)}")
    app.run(host='0.0.0.0', port=port, debug=True)
