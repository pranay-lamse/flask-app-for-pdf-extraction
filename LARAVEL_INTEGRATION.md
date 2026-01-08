# Laravel Integration Guide

## API Endpoint

**Base URL**: `https://your-railway-app.railway.app` (or `http://localhost:5000` for local)

**Extraction Endpoint**: `POST /api/extract`

## Laravel Integration Example

### 1. Send PDF from Laravel

```php
<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;
use Illuminate\Http\UploadedFile;

class PdfExtractionService
{
    private $apiUrl;

    public function __construct()
    {
        // Set this in your .env file: PDF_EXTRACTION_API_URL
        $this->apiUrl = config('services.pdf_extraction.url', 'http://localhost:5000');
    }

    /**
     * Extract crime statistics from PDF
     *
     * @param UploadedFile $pdfFile
     * @return array
     */
    public function extractCrimeStats(UploadedFile $pdfFile)
    {
        try {
            $response = Http::timeout(120) // 2 minutes timeout for large PDFs
                ->attach('file', file_get_contents($pdfFile->getRealPath()), $pdfFile->getClientOriginalName())
                ->post("{$this->apiUrl}/api/extract");

            if ($response->successful()) {
                return $response->json();
            }

            throw new \Exception("PDF extraction failed: " . $response->body());

        } catch (\Exception $e) {
            \Log::error("PDF Extraction Error: " . $e->getMessage());
            throw $e;
        }
    }
}
```

### 2. Controller Example

```php
<?php

namespace App\Http\Controllers;

use App\Services\PdfExtractionService;
use Illuminate\Http\Request;
use App\Models\ReportUpload;
use App\Models\CrimeStatistic;
use App\Models\CrimeHead;
use App\Models\PendingCasesByHead;
use App\Models\ConvictionStats;
use Illuminate\Support\Facades\DB;

class ReportUploadController extends Controller
{
    private $pdfExtractor;

    public function __construct(PdfExtractionService $pdfExtractor)
    {
        $this->pdfExtractor = $pdfExtractor;
    }

    public function upload(Request $request)
    {
        $request->validate([
            'file' => 'required|file|mimes:pdf|max:16384', // 16MB max
            'year' => 'required|integer',
            'month' => 'nullable|integer|min:1|max:12',
        ]);

        DB::beginTransaction();
        
        try {
            // 1. Save file and create report record
            $file = $request->file('file');
            $filePath = $file->store('reports', 'public');
            
            $report = ReportUpload::create([
                'file_path' => $filePath,
                'original_name' => $file->getClientOriginalName(),
                'year' => $request->year,
                'month' => $request->month,
                'status' => 'processing',
            ]);

            // 2. Send to Python API for extraction
            $extractionResult = $this->pdfExtractor->extractCrimeStats($file);

            if (!$extractionResult['success']) {
                throw new \Exception("Extraction failed");
            }

            // 3. Save extracted data to database
            foreach ($extractionResult['data'] as $pageData) {
                $this->saveCrimeStatistics($report->id, $pageData, $request->year, $request->month);
            }

            // 4. Update report status
            $report->update([
                'status' => 'completed',
                'total_pages' => $extractionResult['total_pages']
            ]);

            DB::commit();

            return response()->json([
                'success' => true,
                'message' => 'Report processed successfully',
                'report_id' => $report->id,
                'total_pages' => $extractionResult['total_pages']
            ]);

        } catch (\Exception $e) {
            DB::rollBack();
            
            if (isset($report)) {
                $report->update(['status' => 'failed', 'error_message' => $e->getMessage()]);
            }

            return response()->json([
                'success' => false,
                'error' => $e->getMessage()
            ], 500);
        }
    }

    private function saveCrimeStatistics($reportId, $pageData, $year, $month)
    {
        $period = $month ? date('F', mktime(0, 0, 0, $month, 1)) : 'Full Year';
        
        $crimeStats = $pageData['crime_statistics'] ?? [];
        
        foreach ($crimeStats as $stat) {
            if (empty($stat['crime_head'])) continue;
            
            // Get or create crime head
            $crimeHead = CrimeHead::firstOrCreate(
                ['name' => $stat['crime_head']],
                ['category' => 'Other']
            );
            
            // Calculate detection percentage
            $registered = $stat['registered'] ?? 0;
            $detected = $stat['detected'] ?? 0;
            $detectionPercent = $registered > 0 ? round(($detected / $registered) * 100, 2) : 0;
            
            // Insert crime statistics
            CrimeStatistic::create([
                'report_upload_id' => $reportId,
                'crime_head_id' => $crimeHead->id,
                'year' => $year,
                'period' => $period,
                'registered' => $registered,
                'detected' => $detected,
                'detection_percent' => $detectionPercent,
                'page_number' => $pageData['page_number'] ?? 0
            ]);
            
            // Insert pending cases
            PendingCasesByHead::create([
                'report_upload_id' => $reportId,
                'crime_head_id' => $crimeHead->id,
                'month_0_3' => $stat['pending_0_3'] ?? 0,
                'month_3_6' => $stat['pending_3_6'] ?? 0,
                'month_6_12' => $stat['pending_6_12'] ?? 0,
                'above_1_year' => $stat['pending_1_year'] ?? 0,
            ]);
        }
        
        // Save conviction stats
        if (isset($pageData['conviction_stats'])) {
            $conviction = $pageData['conviction_stats'];
            $decided = $conviction['decided'] ?? 0;
            $convicted = $conviction['convicted'] ?? 0;
            $acquitted = $conviction['acquitted'] ?? 0;
            
            if ($decided == 0 && ($convicted + $acquitted) > 0) {
                $decided = $convicted + $acquitted;
            }
            
            $convictionPercent = $decided > 0 ? round(($convicted / $decided) * 100, 2) : 0;
            
            ConvictionStats::create([
                'report_upload_id' => $reportId,
                'year' => $year,
                'decided' => $decided,
                'convicted' => $convicted,
                'acquitted' => $acquitted,
                'conviction_percent' => $convictionPercent,
                'page_number' => $pageData['page_number'] ?? 0
            ]);
        }
    }
}
```

### 3. Config Setup (config/services.php)

```php
return [
    // ... other services
    
    'pdf_extraction' => [
        'url' => env('PDF_EXTRACTION_API_URL', 'http://localhost:5000'),
    ],
];
```

### 4. Laravel .env

```bash
PDF_EXTRACTION_API_URL=https://your-railway-app.railway.app
```

## JSON Response Structure

```json
{
    "success": true,
    "filename": "crime_report_sept_2024.pdf",
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
                },
                {
                    "crime_head": "Rape",
                    "registered": 30,
                    "detected": 28,
                    "pending_0_3": 2,
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
```

## Testing the API

### Using cURL
```bash
curl -X POST http://localhost:5000/api/extract \
  -F "file=@/path/to/report.pdf"
```

### Using Postman
1. Method: POST
2. URL: `http://localhost:5000/api/extract`
3. Body: form-data
4. Key: `file` (type: File)
5. Value: Select your PDF file

## Error Handling

The API returns errors in this format:
```json
{
    "success": false,
    "error": "Error description"
}
```

Common errors:
- `No file provided` - Missing file in request
- `Only PDF files are allowed` - Invalid file type
- `Gemini API key not configured` - Server configuration issue
- `Processing failed: ...` - Extraction or processing error

## Deployment

1. **Deploy Flask API to Railway**
2. **Get Railway URL** (e.g., `https://your-app.railway.app`)
3. **Set in Laravel .env**: `PDF_EXTRACTION_API_URL=https://your-app.railway.app`
4. **Test the integration**

## Notes

- Maximum file size: 16MB
- Timeout: Set to at least 120 seconds for large PDFs
- The API processes each page sequentially
- JSON structure includes hierarchical crime heads (e.g., "Dacoity - Prof.")
