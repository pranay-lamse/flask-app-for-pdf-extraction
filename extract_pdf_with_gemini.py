import os
import json
import base64
from pdf2image import convert_from_path
import requests
from pathlib import Path

# Configure Gemini API
API_KEY = "AIzaSyDmruExS4O2OqNr_yBJXBzaUDv0pPDD1Cc"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={API_KEY}"

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
    # Default prompt if none provided
    if prompt is None:
        prompt = """
        Analyze this PDF page and extract all the information in a structured JSON format.
        
        Please provide:
        1. Title/Header information
        2. Tables (if any) with all rows and columns
        3. Text content organized by sections
        4. Any numerical data or statistics
        5. Dates, locations, or other metadata
        
        Return the response as valid JSON only, no additional text.
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
    
    if not os.path.exists(pdf_path):
        print(f"Error: PDF file not found: {pdf_path}")
        return
    
    print("="*60)
    print("PDF Content Extraction with Gemini Flash")
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
    
    # Print summary
    print(f"\nSummary:")
    print(f"  - Total pages processed: {len(results)}")
    successful = sum(1 for r in results if 'error' not in r)
    print(f"  - Successfully extracted: {successful}")
    if successful < len(results):
        print(f"  - Failed: {len(results) - successful}")
    
    # Step 4: Cleanup temporary images
    cleanup_temp_images()
    
    print(f"\nYou can now view the extracted data in: {output_file}")

if __name__ == "__main__":
    main()
