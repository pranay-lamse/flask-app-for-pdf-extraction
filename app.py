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
app.config['TEMP_IMAGES_FOLDER'] = 'temp_images'
ALLOWED_EXTENSIONS = {'pdf'}

# Create required folders
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['TEMP_IMAGES_FOLDER'], exist_ok=True)

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

def get_storage_path(filename):
    """Generate organized storage path for PDF images"""
    # Remove extension and sanitize filename
    name_without_ext = os.path.splitext(filename)[0]
    # Remove any special characters that might cause issues
    sanitized_name = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in name_without_ext)
    # Create timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    # Create folder name: {sanitized_filename}_{timestamp}
    folder_name = f"{sanitized_name}_{timestamp}"
    # Full path
    storage_path = os.path.join(app.config['TEMP_IMAGES_FOLDER'], folder_name)
    # Create directory
    os.makedirs(storage_path, exist_ok=True)
    return storage_path

def pdf_to_images(pdf_path, output_folder):
    """Convert PDF pages to images"""
    # Reduced DPI from 300 to 200 for faster processing/upload
    images = convert_from_path(pdf_path, dpi=200)
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

def get_dynamic_prompt():
    return """
    You are an expert data extraction AI. Extract the table data from the image into a clean JSON format.

    ### CRITICAL TABLE ANALYSIS RULES:

    1. **Detect Table Structure:**
        - **Nested Columns:** If headers are stacked (e.g., "2023" over "Reg"), combine them into a single key (e.g., "2023_Reg").
        - **Section Rows:** If a row acts as a title for the rows below it (e.g., a row saying only "Year 2022" followed by data rows), capture that text as a "section" or "year" field for the data rows.

    2. **Row Extraction:**
        - Extract every single data row.
        - Use the column headers exactly as they appear in the image for JSON keys.
        - If the table has a serial number (Sr. No), include it.

    3. **Data Cleaning:**
        - If a value is numeric, return it as a number (e.g., 7641, not "7641").
        - If a value is a percentage, keep the string format (e.g., "47.00%").
        - If a cell is empty or "-", return `null`.

    ### OUTPUT FORMAT (Strict JSON):
    Return ONLY a JSON object. No markdown.

    Example for Row-based Sections (like Conviction Rate table):
    {
      "page_title": "Conviction Rate Status of Nagpur Rural",
      "data": [
        {
          "sr_no": 1,
          "district": "Nagpur (R)",
          "section_year": "Year 2022",  <-- Extracted from the row separator
          "decided": 7641,
          "convicted": 3591,
          "acquitted": 4050,
          "conviction_percentage": "47.00%"
        },
        {
          "sr_no": 2,
          "district": "Nagpur (R)",
          "section_year": "Year 2023",
          "decided": 9546,
          ...
        }
      ]
    }

    Example for Column-based Headers (like Crime Stats table):
    {
      "page_title": "Crime Part I to V",
      "data": [
        {
          "head": "Murder",
          "year_2023_reg": 58,
          "year_2023_det": 56
        }
      ]
    }
    """

def extract_pdf_content_streaming(image_paths, custom_prompt=None):
    """
    Generator function that yields page results as they are processed.
    Used for streaming endpoint.
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
    
    prompt = custom_prompt if custom_prompt else get_dynamic_prompt()
    
    for idx, image_path in enumerate(image_paths):
        current_page_num = idx + 1
        print(f"📄 Processing page {current_page_num}/{len(image_paths)}...", flush=True)
        
        
        # Retry configuration
        max_retries = 3
        retry_delay = 2  # Start with 2 seconds
        
        for attempt in range(max_retries + 1):
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
                
                if attempt > 0:
                    print(f"   🔄 Retry attempt {attempt}/{max_retries} for page {current_page_num}...", flush=True)
                
                response = requests.post(
                    GEMINI_API_URL,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=120
                )
                
                # Handle 503 (Overloaded) and 429 (Rate Limit) errors
                if response.status_code in [503, 529, 429]:
                    if attempt < max_retries:
                        print(f"   ⏳ High traffic (Status {response.status_code}). Retrying in {retry_delay}s...", flush=True)
                        import time
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                        continue
                
                if response.status_code == 200:
                    response_data = response.json()
                    
                    if 'candidates' in response_data and len(response_data['candidates']) > 0:
                        content = response_data['candidates'][0]['content']['parts'][0]['text']
                        
                        try:
                            result = json.loads(content)
                            
                            # Keep result as-is for Laravel to handle
                            # result contains 'category', 'rows', 'conviction_stats'
                            
                            result['type'] = 'page'
                            result['page_number'] = current_page_num
                            print(f"   ✅ Page {current_page_num} extracted successfully", flush=True)
                            yield result
                            break # Success - exit retry loop
                                
                        except json.JSONDecodeError:
                            print(f"   ⚠️  Failed to parse JSON from page {current_page_num}", flush=True)
                            yield {
                                'type': 'page',
                                'page_number': current_page_num,
                                'raw_response': content,
                                'error': 'JSON parsing failed'
                            }
                            break # Don't retry parsing errors
                    else:
                        print(f"   ❌ Page {current_page_num}: No valid response from API. Response: {str(response_data)[:200]}", flush=True)
                        yield {
                            'type': 'page',
                            'page_number': current_page_num,
                            'error': 'No valid response from API'
                        }
                        break # Don't retry if response is valid but empty
                else:
                    error_msg = response.json() if response.text else {"error": "Unknown error"}
                    print(f"   ❌ Page {current_page_num}: API request failed: {response.status_code}. Error: {error_msg}", flush=True)
                    
                    # If it's a fatal error (not 503/429) or last retry failed
                    if attempt == max_retries:
                        yield {
                            'type': 'page',
                            'page_number': current_page_num,
                            'error': f'API request failed: {response.status_code}',
                            'error_details': error_msg
                        }
                    # If 503/429, loop will retry naturally
                    
            except Exception as e:
                print(f"   ❌ Error processing page {current_page_num}: {str(e)}", flush=True)
                if attempt == max_retries:
                    yield {
                        'type': 'page',
                        'page_number': current_page_num,
                        'error': str(e)
                    }

def extract_pdf_content_with_gemini(image_paths, custom_prompt=None):
    """Extract content from PDF images using Gemini API (non-streaming version)"""
    
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
    
    # Use custom prompt if provided, otherwise use dynamic prompt
    prompt = custom_prompt if custom_prompt else get_dynamic_prompt()
    
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
                timeout=120
            )
            
            if response.status_code == 200:
                response_data = response.json()
                
                if 'candidates' in response_data and len(response_data['candidates']) > 0:
                    content = response_data['candidates'][0]['content']['parts'][0]['text']
                    
                    try:
                        result = json.loads(content)
                        # Keep result as-is for Laravel to handle
                        # result contains 'category', 'rows', 'conviction_stats'
                        
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
    STREAMING ENDPOINT - Process PDFs Page-by-Page
    
    Streams extraction results as they become available.
    Recommended for large files to avoid timeouts, but works for PDFs of ANY size.
    
    Request (multipart/form-data):
        - file: PDF file (required)
        - prompt: Optional custom extraction prompt (overrides the default dynamic prompt)
    
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
    
    # Save file IMMEDIATELY before streaming starts
    try:
        filename = secure_filename(file.filename)
        
        # Add timestamp if file already exists
        if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
            name, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{name}_{timestamp}{ext}"
        
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(pdf_path)
        print(f"📥 Saved PDF: {filename}")
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to save file: {str(e)}'
        }), 500
    
    def generate():
        try:
            # Create persistent storage folder for images (organized by PDF name + timestamp)
            image_folder = get_storage_path(filename)
            print(f"📁 Storing images in: {image_folder}")
            
            # Convert PDF to images (images will be kept for debugging)
            image_paths = pdf_to_images(pdf_path, image_folder)
            
            total_pages = len(image_paths)
            print(f"🖼️  Converted to {total_pages} images (stored permanently)")
            
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
        
        finally:
            # Delete the uploaded PDF file (but keep images for debugging)
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                    print(f"♻️  Deleted temp PDF: {filename}", flush=True)
                    print(f"✅ Images preserved in: {image_folder}", flush=True)
                except Exception as e:
                    print(f"⚠️  Failed to delete temp PDF: {str(e)}", flush=True)
    
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
        
        # Create persistent storage folder for images (organized by PDF name + timestamp)
        image_folder = get_storage_path(filename)
        print(f"📁 Storing images in: {image_folder}")
        
        # Convert PDF to images (images will be kept for debugging)
        image_paths = pdf_to_images(pdf_path, image_folder)
        
        print(f"🖼️  Converted to {len(image_paths)} images (stored permanently)")
        
        # Extract content using Gemini
        results = extract_pdf_content_with_gemini(image_paths, custom_prompt)
        
        print(f"✅ Extraction complete! Returning {len(results)} pages")
        print(f"✅ Images preserved in: {image_folder}")
        
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
    
    finally:
        # Delete the uploaded PDF file
        if 'pdf_path' in locals() and os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
                print(f"♻️  Deleted temp PDF: {filename}")
            except Exception as e:
                print(f"⚠️  Failed to delete temp PDF: {str(e)}")

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
