import os
import json
import base64
import tempfile
from flask import Flask, request, jsonify
from flask_cors import CORS
from pdf2image import convert_from_path
import requests
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
ALLOWED_EXTENSIONS = {'pdf'}

# Get API key from environment variable
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash-lite')
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

@app.route('/', methods=['GET'])
def index():
    """API status endpoint"""
    return jsonify({
        'status': 'online',
        'api': 'PDF Content Extraction with Gemini',
        'version': '1.0',
        'endpoints': {
            '/': 'API status (GET)',
            '/extract': 'Extract PDF content (POST)',
            '/health': 'Health check (GET)'
        }
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for Railway"""
    api_key_set = bool(GEMINI_API_KEY)
    return jsonify({
        'status': 'healthy',
        'gemini_api_configured': api_key_set,
        'model': GEMINI_MODEL
    })

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
        # Create temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            # Save uploaded PDF
            filename = secure_filename(file.filename)
            pdf_path = os.path.join(temp_dir, filename)
            file.save(pdf_path)
            
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
