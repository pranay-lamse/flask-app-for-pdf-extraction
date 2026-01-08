# PDF Crime Statistics Extraction API

Simple Flask API for extracting crime statistics from PDF files using Google Gemini AI.

## 🚀 For Laravel Integration

### API Endpoint

```
POST https://your-railway-app.railway.app/api/extract
```

**Request**: Send PDF as `multipart/form-data`
- Field name: `file`
- File type: PDF only
- Max size: 16MB

**Response**: JSON with extracted crime statistics

```json
{
    "success": true,
    "filename": "report.pdf",
    "total_pages": 2,
    "data": [...]
}
```

📖 **Full Laravel Integration Guide**: See [LARAVEL_INTEGRATION.md](./LARAVEL_INTEGRATION.md)

## 🛠️ Local Setup

1. **Install dependencies**:
```bash
pip install -r requirements.txt
```

2. **Install Poppler** (for PDF conversion):
   - Windows: [Download here](https://github.com/oschwartz10612/poppler-windows/releases/)
   - Linux: `sudo apt-get install poppler-utils`
   - macOS: `brew install poppler`

3. **Create `.env` file**:
```bash
GEMINI_API_KEY=your_google_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash-lite
PORT=5000
```

4. **Run**:
```bash
python app.py
```

API will be at `http://localhost:5000`

## 🧪 Test the API

```bash
curl -X POST http://localhost:5000/api/extract \
  -F "file=@your_report.pdf"
```

## 🚂 Railway Deployment

1. Push to GitHub
2. Create new project on [Railway](https://railway.app)
3. Connect your GitHub repo
4. Set environment variable: `GEMINI_API_KEY`
5. Railway auto-deploys!

Your API URL: `https://your-app.railway.app/api/extract`

## 📝 Environment Variables

- `GEMINI_API_KEY` (required) - Your Google Gemini API key
- `GEMINI_MODEL` (optional) - Default: `gemini-2.5-flash-lite`
- `PORT` (optional) - Default: `5000`

## 📄 Files

- `app.py` - Main Flask API
- `requirements.txt` - Python dependencies
- `Procfile` - Railway deployment config
- `nixpacks.toml` - Poppler dependency config
- `.env.example` - Environment template
- `LARAVEL_INTEGRATION.md` - Complete PHP/Laravel code examples

## ✅ What's Extracted

For each PDF page:
- Crime statistics (hierarchical crime heads)
- Registered / Detected cases
- Pending cases by time period
- Conviction statistics

Laravel handles database storage - this API only returns JSON!