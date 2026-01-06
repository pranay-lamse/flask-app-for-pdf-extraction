
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
GEMINI_API_KEY =  'AIzaSyDmruExS4O2OqNr_yBJXBzaUDv0pPDD1Cc'
GEMINI_MODEL = 'gemini-2.5-flash'

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

def extract_pdf_content_with_gemini(image_paths, custom_prompt=None):
    """Extract content from PDF images using Gemini API"""
    default_prompt = """
    Analyze this PDF page and extract all the information in a structured JSON format.
    
    Please provide:
    1. Title/Header information
    2. Tables (if any) with all rows and columns
    3. Text content organized by sections
    4. Any numerical data or statistics
    5. Dates, locations, or other metadata
    
    Return the response as valid JSON only, no additional text.
    """
    
    prompt = custom_prompt if custom_prompt else default_prompt
    all_results = []
    
    for idx, image_path in enumerate(image_paths):
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
                        result['page_number'] = idx + 1
                        all_results.append(result)
                    except json.JSONDecodeError:
                        all_results.append({
                            'page_number': idx + 1,
                            'raw_response': content,
                            'error': 'JSON parsing failed'
                        })
                else:
                    all_results.append({
                        'page_number': idx + 1,
                        'error': 'No valid response from API',
                        'raw_response': response_data
                    })
            else:
                error_msg = response.json() if response.text else {"error": "Unknown error"}
                all_results.append({
                    'page_number': idx + 1,
                    'error': f'API request failed: {response.status_code}',
                    'error_details': error_msg
                })
                
        except Exception as e:
            all_results.append({
                'page_number': idx + 1,
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

# MySQL Configuration
def get_db_connection():
    try:
        import mysql.connector
        return mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "crimedigest")
        )
    except Exception as e:
        print(f"DB Connection Error: {e}")
        return None

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
            'rows': crime_stats, # For frontend compatibility
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
