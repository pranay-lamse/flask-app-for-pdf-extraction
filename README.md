# PDF Content Extraction API with Flask & Gemini

A Flask web API that extracts structured content from PDF files using Google's Gemini AI with vision capabilities.

## 🚀 Features

- **PDF Upload API** - Upload PDF files and get structured JSON responses
- **Gemini Vision AI** - Uses Gemini 2.5 Flash Lite for fast, accurate extraction
- **Table Extraction** - Automatically extracts tables with proper structure
- **Multi-page Support** - Processes all pages in a PDF document
- **Railway Ready** - Configured for one-click Railway deployment
- **CORS Enabled** - Works with frontend applications

## 📋 API Endpoints

### `GET /`
API status and available endpoints

### `GET /health`
Health check endpoint (useful for Railway monitoring)

### `POST /extract`
Extract content from PDF

**Request:**
- Method: `POST`
- Content-Type: `multipart/form-data`
- Body:
  - `file` (required): PDF file
  - `prompt` (optional): Custom extraction prompt

**Response:**
```json
{
  "success": true,
  "filename": "document.pdf",
  "total_pages": 2,
  "data": [
    {
      "page_number": 1,
      "title": "...",
      "tables": [...],
      "metadata": {...}
    }
  ]
}
```

## 🛠️ Local Development

### Prerequisites

1. **Python 3.8+**
2. **Poppler** (for PDF to image conversion)
   - Windows: [Download Poppler](https://github.com/oschwartz10612/poppler-windows/releases/)
   - Linux: `sudo apt-get install poppler-utils`
   - macOS: `brew install poppler`

### Setup

1. **Clone and install dependencies:**
```bash
pip install -r requirements.txt
```

2. **Create `.env` file:**
```bash
cp .env.example .env
```

3. **Add your Gemini API key to `.env`:**
```
GEMINI_API_KEY=your_actual_api_key_here
GEMINI_MODEL=gemini-2.5-flash-lite
PORT=5000
```

4. **Run the app:**
```bash
python app.py
```

The API will be available at `http://localhost:5000`

### Test the API

**Using curl:**
```bash
curl -X POST http://localhost:5000/extract \
  -F "file=@your-document.pdf"
```

**Using Python requests:**
```python
import requests

url = "http://localhost:5000/extract"
files = {"file": open("document.pdf", "rb")}

response = requests.post(url, files=files)
print(response.json())
```

## 🚂 Railway Deployment

### Quick Deploy

1. **Push to GitHub:**
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin your-repo-url
git push -u origin main
```

2. **Deploy on Railway:**
   - Go to [railway.app](https://railway.app)
   - Click "New Project" → "Deploy from GitHub repo"
   - Select your repository
   - Railway will auto-detect the `Procfile` and deploy

3. **Set Environment Variables:**
   - In Railway dashboard, go to your project
   - Click "Variables"
   - Add:
     - `GEMINI_API_KEY`: Your Gemini API key
     - `GEMINI_MODEL`: `gemini-2.5-flash-lite` (optional, defaults to this)

4. **Install Poppler (Important!):**
   Railway needs Poppler for PDF processing. Add this to your Railway project:
   
   In Railway dashboard → Settings → Add Nixpacks build configuration:
   ```
   nixPacks.pkgs = ["poppler_utils"]
   ```

   Or create a `nixpacks.toml` file:
   ```toml
   [phases.setup]
   nixPkgs = ["poppler_utils"]
   ```

### Alternative: Using railway.json

Create `railway.json`:
```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "gunicorn app:app",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

## 📝 Example Response

```json
{
  "success": true,
  "filename": "crime-report.pdf",
  "total_pages": 2,
  "data": [
    {
      "title": "Crime IPC / BNS",
      "metadata": {
        "page_number": 1,
        "reporting_periods": ["Year 2023", "2024", "upto 30 SEPT. 2025"]
      },
      "crime_statistics_table": {
        "headers": {...},
        "rows": [
          {
            "head": "Murder",
            "year_2023": {"reg": 25, "det": 24, "pct": "96%"},
            "year_2024": {"reg": 13, "det": 13, "pct": "100%"}
          }
        ]
      },
      "page_number": 1
    }
  ]
}
```

## 🔧 Configuration

Environment variables:

- `GEMINI_API_KEY` (required): Your Google Gemini API key
- `GEMINI_MODEL` (optional): Model to use (default: `gemini-2.5-flash-lite`)
- `PORT` (optional): Port to run on (default: 5000)

## 📦 Project Structure

```
.
├── app.py                 # Flask application
├── extract_pdf_with_gemini.py  # Standalone CLI tool
├── requirements.txt       # Python dependencies
├── Procfile              # Railway/Heroku deployment
├── .env.example          # Environment variables template
├── README.md             # This file
└── nixpacks.toml         # (Optional) Nixpacks configuration
```

## 🎯 Use Cases

- Extract data from government reports
- Parse invoice tables
- Extract crime statistics
- Process forms and documents
- Convert PDFs to structured JSON

## 🔐 Security Notes

- Never commit `.env` file or expose your API keys
- The API has a 16MB file size limit for safety
- Only PDF files are accepted
- Files are processed in temporary directories and auto-deleted

## 📄 License

MIT License - feel free to use this in your projects!

## 🤝 Contributing

Feel free to open issues or submit pull requests!

## 📞 Support

If you encounter issues:
1. Check that Poppler is installed correctly
2. Verify your Gemini API key is valid
3. Ensure your PDF is not corrupted
4. Check Railway logs for deployment issues
