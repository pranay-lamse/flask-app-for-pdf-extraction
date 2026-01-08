import os
import json
import base64
from flask import Flask, request, jsonify, render_template, Response, stream_with_context
from flask_cors import CORS
from pdf2image import convert_from_path
import requests
from werkzeug.utils import secure_filename
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for Laravel API calls

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}

# Create uploads folder
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Gemini API Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

if GEMINI_API_KEY:
    print(f"✅ API Key loaded: {GEMINI_API_KEY[:5]}...{GEMINI_API_KEY[-4:]}")
else:
    print("⚠️  Warning: GEMINI_API_KEY not found in .env!")

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

def extract_pdf_content_streaming(image_paths, custom_prompt=None):
    """
    Generator function that yields page results as they are processed.
    Used for streaming endpoint.
    """
    # Crime statistics extraction prompt
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
    
    prompt = custom_prompt if custom_prompt else crime_stats_prompt
    
    for idx, image_path in enumerate(image_paths):
        current_page_num = idx + 1
        print(f"📄 Processing page {current_page_num}/{len(image_paths)}...")
        
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
                        if isinstance(result, list):
                            result = {"crime_statistics": result}
                        elif "rows" in result:
                            result["crime_statistics"] = result["rows"]
                        
                        result['type'] = 'page'
                        result['page_number'] = current_page_num
                        print(f"   ✅ Page {current_page_num} extracted successfully")
                        yield result
                            
                    except json.JSONDecodeError:
                        print(f"   ⚠️  Failed to parse JSON from page {current_page_num}")
                        yield {
                            'type': 'page',
                            'page_number': current_page_num,
                            'raw_response': content,
                            'error': 'JSON parsing failed'
                        }
                else:
                    yield {
                        'type': 'page',
                        'page_number': current_page_num,
                        'error': 'No valid response from API'
                    }
            else:
                error_msg = response.json() if response.text else {"error": "Unknown error"}
                yield {
                    'type': 'page',
                    'page_number': current_page_num,
                    'error': f'API request failed: {response.status_code}',
                    'error_details': error_msg
                }
                
        except Exception as e:
            print(f"   ❌ Error processing page {current_page_num}: {str(e)}")
            yield {
                'type': 'page',
                'page_number': current_page_num,
                'error': str(e)
            }

def extract_pdf_content_with_gemini(image_paths, custom_prompt=None):
    """Extract content from PDF images using Gemini API (non-streaming version)"""
    
    # Crime statistics extraction prompt (optimized for crime report PDFs)
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
    
    # Default general extraction prompt
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
    
    # Use custom prompt if provided, otherwise use crime stats prompt
    prompt = custom_prompt if custom_prompt else crime_stats_prompt
    
    all_results = []
    
    for idx, image_path in enumerate(image_paths):
        current_page_num = idx + 1
        print(f"📄 Processing page {current_page_num}/{len(image_paths)}...")
        
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
                        # Ensure structure for crime stats
                        if isinstance(result, list):
                            result = {"crime_statistics": result}
                        elif "rows" in result:
                            result["crime_statistics"] = result["rows"]
                        
                        result['page_number'] = current_page_num
                        all_results.append(result)
                        print(f"   ✅ Page {current_page_num} extracted successfully")
                            
                    except json.JSONDecodeError:
                        print(f"   ⚠️  Failed to parse JSON from page {current_page_num}")
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
            print(f"   ❌ Error processing page {current_page_num}: {str(e)}")
            all_results.append({
                'page_number': current_page_num,
                'error': str(e)
            })
    
    return all_results

@app.route('/')
def index():
    """API information endpoint"""
    return jsonify({
        'service': 'PDF Crime Statistics Extraction API',
        'version': '1.0',
        'status': 'active',
        'endpoints': {
            'health': 'GET /health',
            'extract': 'POST /api/extract'
        }
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for Railway"""
    return jsonify({
        'status': 'healthy',
        'gemini_api_configured': bool(GEMINI_API_KEY),
        'model': GEMINI_MODEL
    })

@app.route('/api/extract/stream', methods=['POST'])
def extract_pdf_stream():
    """
    STREAMING ENDPOINT - For Large PDFs (85+ pages)
    
    Sends JSON data page-by-page as they are processed.
    Perfect for large PDFs to avoid timeout and show real-time progress.
    
    Request (multipart/form-data):
        - file: PDF file (required)
        - prompt: Optional custom extraction prompt
    
    Response: Server-Sent Events (text/event-stream)
    Each event contains JSON for one processed page:
    
    data: {"type": "start", "filename": "report.pdf", "total_pages": 85}
    
    data: {"type": "page", "page_number": 1, "crime_statistics": [...], "conviction_stats": {...}}
    
    data: {"type": "page", "page_number": 2, "crime_statistics": [...], "conviction_stats": {...}}
    
    ...
    
    data: {"type": "complete", "total_processed": 85}
    """
    # Check if API key is configured
    if not GEMINI_API_KEY:
        return jsonify({
            'success': False,
            'error': 'Gemini API key not configured'
        }), 500
    
    # Check if file is present
    if 'file' not in request.files:
        return jsonify({
            'success': False,
            'error': 'No file provided'
        }), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({
            'success': False,
            'error': 'No file selected'
        }), 400
    
    if not allowed_file(file.filename):
        return jsonify({
            'success': False,
            'error': 'Only PDF files are allowed'
        }), 400
    
    # Get optional custom prompt
    custom_prompt = request.form.get('prompt', None)
    
    def generate():
        try:
            # Save uploaded PDF
            filename = secure_filename(file.filename)
            
            # Add timestamp if file already exists
            if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
                name, ext = os.path.splitext(filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"{name}_{timestamp}{ext}"
            
            pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(pdf_path)
            
            print(f"📥 Received PDF: {filename}")
            
            # Create temporary directory for image processing
            import tempfile
            with tempfile.TemporaryDirectory() as temp_dir:
                # Convert PDF to images
                image_folder = os.path.join(temp_dir, 'images')
                os.makedirs(image_folder, exist_ok=True)
                image_paths = pdf_to_images(pdf_path, image_folder)
                
                total_pages = len(image_paths)
                print(f"🖼️  Converted to {total_pages} images")
                
                # Send start event
                yield f"data: {json.dumps({'type': 'start', 'filename': filename, 'total_pages': total_pages})}\n\n"
                
                # Process each page and stream results
                for result in extract_pdf_content_streaming(image_paths, custom_prompt):
                    yield f"data: {json.dumps(result)}\n\n"
                
                # Send complete event
                yield f"data: {json.dumps({'type': 'complete', 'total_processed': total_pages})}\n\n"
                
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/api/extract', methods=['POST'])
def extract_pdf():
    """
    STANDARD ENDPOINT - For Small to Medium PDFs (1-20 pages)
    
    Processes entire PDF and returns complete JSON at the end.
    
    FOR LARGE PDFs (85+ pages), use /api/extract/stream instead!
    
    Request (multipart/form-data):
        - file: PDF file (required)
        - prompt: Optional custom extraction prompt
    
    Response (JSON):
    {
        "success": true,
        "filename": "report.pdf",
        "total_pages": 2,
        "data": [
            {
                "page_number": 1,
                "crime_statistics": [
                    {
                        "crime_head": "Murder",
                        "registered": 25,
                        "detected": 24,
                        "pending_0_3": 1,
                        "pending_3_6": 0,
                        "pending_6_12": 0,
                        "pending_1_year": 0
                    }
                ],
                "conviction_stats": {
                    "decided": 100,
                    "convicted": 85,
                    "acquitted": 15
                }
            }
        ]
    }
    """
    # Check if API key is configured
    if not GEMINI_API_KEY:
        return jsonify({
            'success': False,
            'error': 'Gemini API key not configured'
        }), 500
    
    # Check if file is present
    if 'file' not in request.files:
        return jsonify({
            'success': False,
            'error': 'No file provided'
        }), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({
            'success': False,
            'error': 'No file selected'
        }), 400
    
    if not allowed_file(file.filename):
        return jsonify({
            'success': False,
            'error': 'Only PDF files are allowed'
        }), 400
    
    # Get optional custom prompt
    custom_prompt = request.form.get('prompt', None)
    
    try:
        # Save uploaded PDF
        filename = secure_filename(file.filename)
        
        # Add timestamp if file already exists
        if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
            name, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{name}_{timestamp}{ext}"
        
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(pdf_path)
        
        print(f"📥 Received PDF: {filename}")
        
        # Create temporary directory for image processing
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            # Convert PDF to images
            image_folder = os.path.join(temp_dir, 'images')
            os.makedirs(image_folder, exist_ok=True)
            image_paths = pdf_to_images(pdf_path, image_folder)
            
            print(f"🖼️  Converted to {len(image_paths)} images")
            
            # Extract content using Gemini
            results = extract_pdf_content_with_gemini(image_paths, custom_prompt)
            
            print(f"✅ Extraction complete! Returning {len(results)} pages")
            
            # Return JSON response for Laravel
            return jsonify({
                'success': True,
                'filename': filename,
                'total_pages': len(results),
                'data': results
            })
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Processing failed: {str(e)}'
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("="*60)
    print("🚀 PDF Crime Statistics Extraction API")
    print("="*60)
    print(f"📡 Server: http://0.0.0.0:{port}")
    print(f"🔑 API Key: {'✅ Configured' if GEMINI_API_KEY else '❌ Missing'}")
    print(f"🤖 Model: {GEMINI_MODEL}")
    print("="*60)
    app.run(host='0.0.0.0', port=port, debug=True)
