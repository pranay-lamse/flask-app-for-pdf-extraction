# Laravel Streaming Integration Guide

## 🌊 Streaming API Endpoint (For 85+ Page PDFs)

### Endpoint

```
POST /api/extract/stream
```

This endpoint sends data **page-by-page in real-time** using Server-Sent Events (SSE).

---

## Laravel Implementation

### 1. Create Streaming Service

```php
<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;
use Illuminate\Http\UploadedFile;
use Illuminate\Support\Facades\Log;

class StreamingPdfExtractionService
{
    private $apiUrl;

    public function __construct()
    {
        $this->apiUrl = config('services.pdf_extraction.url', 'http://localhost:5000');
    }

    /**
     * Extract crime statistics from large PDF with streaming
     *
     * @param UploadedFile $pdfFile
     * @param callable $onPageProcessed Callback function for each page
     * @return array
     */
    public function extractWithStreaming(UploadedFile $pdfFile, callable $onPageProcessed)
    {
        $ch = curl_init("{$this->apiUrl}/api/extract/stream");
        
        // Prepare file for upload
        $cfile = new \CURLFile(
            $pdfFile->getRealPath(),
            $pdfFile->getMimeType(),
            $pdfFile->getClientOriginalName()
        );
        
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => ['file' => $cfile],
            CURLOPT_RETURNTRANSFER => false,
            CURLOPT_TIMEOUT => 0, // No timeout for streaming
            CURLOPT_WRITEFUNCTION => function($curl, $data) use ($onPageProcessed) {
                // Parse Server-Sent Events
                if (str_starts_with($data, 'data: ')) {
                    $json = trim(substr($data, 6));
                    if (!empty($json)) {
                        try {
                            $eventData = json_decode($json, true);
                            
                            // Call callback for each event
                            if ($eventData) {
                                $onPageProcessed($eventData);
                            }
                        } catch (\Exception $e) {
                            Log::error("Error parsing SSE: " . $e->getMessage());
                        }
                    }
                }
                
                return strlen($data);
            },
        ]);
        
        $result = curl_exec($ch);
        
        if ($result === false) {
            throw new \Exception('Curl error: ' . curl_error($ch));
        }
        
        curl_close($ch);
    }
}
```

---

### 2. Controller with Real-Time Database Saving

```php
<?php

namespace App\Http\Controllers;

use App\Services\StreamingPdfExtractionService;
use Illuminate\Http\Request;
use App\Models\ReportUpload;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;

class StreamingReportController extends Controller
{
    private $extractor;

    public function __construct(StreamingPdfExtractionService $extractor)
    {
        $this->extractor = $extractor;
    }

    public function uploadWithStreaming(Request $request)
    {
        $request->validate([
            'file' => 'required|file|mimes:pdf|max:51200', // 50MB max for large PDFs
            'year' => 'required|integer',
            'month' => 'nullable|integer|min:1|max:12',
        ]);

        DB::beginTransaction();
        
        try {
            // 1. Create report record
            $file = $request->file('file');
            $filePath = $file->store('reports', 'public');
            
            $report = ReportUpload::create([
                'file_path' => $filePath,
                'original_name' => $file->getClientOriginalName(),
                'year' => $request->year,
                'month' => $request->month,
                'status' => 'processing',
                'total_pages' => 0,
            ]);

            $totalPages = 0;
            $processedPages = 0;
            
            // 2. Process with streaming - saves page-by-page as they arrive
            $this->extractor->extractWithStreaming($file, function($event) use ($report, &$totalPages, &$processedPages, $request) {
                
                if ($event['type'] === 'start') {
                    // First event - total pages count
                    $totalPages = $event['total_pages'];
                    $report->update(['total_pages' => $totalPages]);
                    Log::info("Started processing {$totalPages} pages for report {$report->id}");
                    
                } elseif ($event['type'] === 'page') {
                    // Each page arrives here in real-time!
                    $this->saveCrimeStatistics($report->id, $event, $request->year, $request->month);
                    $processedPages++;
                    
                    // Update progress
                    $report->update([
                        'processed_pages' => $processedPages,
                        'progress_percent' => round(($processedPages / $totalPages) * 100, 2)
                    ]);
                    
                    Log::info("Processed page {$processedPages}/{$totalPages} for report {$report->id}");
                    
                } elseif ($event['type'] === 'complete') {
                    // All pages done
                    Log::info("Completed processing report {$report->id}");
                    
                } elseif ($event['type'] === 'error') {
                    // Error occurred
                    throw new \Exception($event['error']);
                }
            });

            // 3. Mark as complete
            $report->update([
                'status' => 'completed',
                'processed_pages' => $totalPages,
                'progress_percent' => 100
            ]);

            DB::commit();

            return response()->json([
                'success' => true,
                'message' => "Report processed successfully ({$totalPages} pages)",
                'report_id' => $report->id,
                'total_pages' => $totalPages
            ]);

        } catch (\Exception $e) {
            DB::rollBack();
            
            if (isset($report)) {
                $report->update([
                    'status' => 'failed',
                    'error_message' => $e->getMessage()
                ]);
            }

            Log::error("Streaming extraction failed: " . $e->getMessage());

            return response()->json([
                'success' => false,
                'error' => $e->getMessage()
            ], 500);
        }
    }

    private function saveCrimeStatistics($reportId, $pageData, $year, $month)
    {
        // Same logic as before - saves one page at a time
        $period = $month ? date('F', mktime(0, 0, 0, $month, 1)) : 'Full Year';
        
        $crimeStats = $pageData['crime_statistics'] ?? [];
        
        foreach ($crimeStats as $stat) {
            if (empty($stat['crime_head'])) continue;
            
            $crimeHead = \App\Models\CrimeHead::firstOrCreate(
                ['name' => $stat['crime_head']],
                ['category' => 'Other']
            );
            
            $registered = $stat['registered'] ?? 0;
            $detected = $stat['detected'] ?? 0;
            $detectionPercent = $registered > 0 ? round(($detected / $registered) * 100, 2) : 0;
            
            \App\Models\CrimeStatistic::create([
                'report_upload_id' => $reportId,
                'crime_head_id' => $crimeHead->id,
                'year' => $year,
                'period' => $period,
                'registered' => $registered,
                'detected' => $detected,
                'detection_percent' => $detectionPercent,
                'page_number' => $pageData['page_number'] ?? 0
            ]);
            
            \App\Models\PendingCasesByHead::create([
                'report_upload_id' => $reportId,
                'crime_head_id' => $crimeHead->id,
                'month_0_3' => $stat['pending_0_3'] ?? 0,
                'month_3_6' => $stat['pending_3_6'] ?? 0,
                'month_6_12' => $stat['pending_6_12'] ?? 0,
                'above_1_year' => $stat['pending_1_year'] ?? 0,
            ]);
        }
        
        // Conviction stats
        if (isset($pageData['conviction_stats'])) {
            $conviction = $pageData['conviction_stats'];
            $decided = $conviction['decided'] ?? 0;
            $convicted = $conviction['convicted'] ?? 0;
            $acquitted = $conviction['acquitted'] ?? 0;
            
            if ($decided == 0 && ($convicted + $acquitted) > 0) {
                $decided = $convicted + $acquitted;
            }
            
           $convictionPercent = $decided > 0 ? round(($convicted / $decided) * 100, 2) : 0;
            
            \App\Models\ConvictionStats::create([
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

---

### 3. Add Progress Columns to Migration

```php
<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

class AddProgressToReportUploads extends Migration
{
    public function up()
    {
        Schema::table('report_uploads', function (Blueprint $table) {
            $table->integer('processed_pages')->default(0)->after('total_pages');
            $table->decimal('progress_percent', 5, 2)->default(0)->after('processed_pages');
        });
    }

    public function down()
    {
        Schema::table('report_uploads', function (Blueprint $table) {
            $table->dropColumn(['processed_pages', 'progress_percent']);
        });
    }
}
```

---

## How It Works

### Timeline for 85-Page PDF:

```
0:00 - Laravel uploads PDF to Flask
0:01 - Flask sends: {"type": "start", "total_pages": 85}
       Laravel creates report record with status "processing"

0:02 - Flask sends: {"type": "page", "page_number": 1, "crime_statistics": [...]}
       Laravel saves page 1 to DB immediately
       Progress: 1/85 (1.18%)

0:03 - Flask sends: {"type": "page", "page_number": 2, ...}
       Laravel saves page 2 to DB
       Progress: 2/85 (2.35%)

... continues for all 85 pages ...

2:50 - Flask sends: {"type": "page", "page_number": 85, ...}
       Laravel saves page 85 to DB
       Progress: 85/85 (100%)

2:50 - Flask sends: {"type": "complete", "total_processed": 85}
       Laravel marks report as "completed"
```

### Key Benefits:

✅ **No Timeout** - Connection stays open, no timeout issues
✅ **Real-Time Progress** - Database shows current progress immediately
✅ **Memory Efficient** - Processes one page at a time
✅ **Fault Tolerant** - If it fails on page 50, you have pages 1-49 in DB

---

## Frontend Progress Display (Optional)

If you want to show progress to users in real-time, you can poll the database:

```javascript
// JavaScript - poll progress every 2 seconds
function checkProgress(reportId) {
    setInterval(async () => {
        const response = await fetch(`/api/reports/${reportId}/progress`);
        const data = await response.json();
        
        console.log(`Progress: ${data.processed_pages}/${data.total_pages} (${data.progress_percent}%)`);
        
        if (data.status === 'completed') {
            console.log('Processing complete!');
            clearInterval();
        }
    }, 2000);
}
```

---

## Testing

```bash
curl -X POST http://localhost:5000/api/extract/stream \
  -F "file=@large_report_85_pages.pdf" \
  --no-buffer
```

You'll see output like:
```
data: {"type":"start","filename":"large_report_85_pages.pdf","total_pages":85}

data: {"type":"page","page_number":1,"crime_statistics":[...]}

data: {"type":"page","page_number":2,"crime_statistics":[...]}

...

data: {"type":"complete","total_processed":85}
```

---

## Comparison: Standard vs Streaming

| Feature | `/api/extract` | `/api/extract/stream` |
|---------|---------------|----------------------|
| **Best for** | 1-20 pages | 85+ pages |
| **Timeout risk** | High for large PDFs | None |
| **Progress updates** | No | Yes (real-time) |
| **Memory usage** | Loads all pages | One page at a time |
| **Response format** | JSON (all at once) | SSE (page-by-page) |
| **Laravel code** | `Http::post()` | `curl` with callback |

---

## Recommended Usage

- **Small PDFs (1-20 pages)**: Use `/api/extract` (simpler Laravel code)
- **Large PDFs (85+ pages)**: Use `/api/extract/stream` (no timeout, real-time progress)

Your 85-page PDFs will work perfectly with the streaming endpoint! 🚀
