# Laravel Developer Guide - Send PDF to Flask API

## Scenario

You have a table `report_uploads` with PDF file path stored. User clicks "Extract" button, Laravel sends PDF to Flask API for processing.

---

## Step 1: Add Button in Your View

```blade
<!-- In your reports list view (e.g., resources/views/reports/index.blade.php) -->

<table>
    <thead>
        <tr>
            <th>ID</th>
            <th>File Name</th>
            <th>Year</th>
            <th>Month</th>
            <th>Status</th>
            <th>Action</th>
        </tr>
    </thead>
    <tbody>
        @foreach($reports as $report)
        <tr>
            <td>{{ $report->id }}</td>
            <td>{{ $report->original_name }}</td>
            <td>{{ $report->year }}</td>
            <td>{{ $report->month }}</td>
            <td>
                <span class="badge badge-{{ $report->status == 'completed' ? 'success' : 'warning' }}">
                    {{ $report->status }}
                </span>
                @if($report->status == 'processing')
                    ({{ $report->progress_percent }}%)
                @endif
            </td>
            <td>
                @if($report->status == 'pending' || $report->status == 'failed')
                    <form action="{{ route('reports.extract', $report->id) }}" method="POST" style="display:inline;">
                        @csrf
                        <button type="submit" class="btn btn-primary btn-sm">
                            Extract Data
                        </button>
                    </form>
                @endif
            </td>
        </tr>
        @endforeach
    </tbody>
</table>
```

---

## Step 2: Add Route

```php
// routes/web.php

use App\Http\Controllers\ReportExtractionController;

Route::post('/reports/{report}/extract', [ReportExtractionController::class, 'extractData'])
    ->name('reports.extract');

// Optional: Progress check endpoint
Route::get('/api/reports/{report}/progress', [ReportExtractionController::class, 'getProgress'])
    ->name('reports.progress');
```

---

## Step 3: Create Controller

```php
<?php

namespace App\Http\Controllers;

use App\Models\ReportUpload;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Storage;
use Illuminate\Support\Facades\Log;

class ReportExtractionController extends Controller
{
    private $flaskApiUrl;

    public function __construct()
    {
        // Set this in .env: FLASK_API_URL=https://your-railway-app.railway.app
        $this->flaskApiUrl = env('FLASK_API_URL', 'http://localhost:5000');
    }

    /**
     * Extract data from PDF using Flask API (Streaming for large PDFs)
     */
    public function extractData(ReportUpload $report)
    {
        // Validate report exists and has a file
        if (!$report->file_path || !Storage::exists($report->file_path)) {
            return back()->with('error', 'PDF file not found');
        }

        // Update status to processing
        $report->update([
            'status' => 'processing',
            'processed_pages' => 0,
            'progress_percent' => 0,
            'error_message' => null
        ]);

        // Get the actual file path
        $filePath = Storage::path($report->file_path);
        
        try {
            // Send to Flask API with streaming
            $this->sendToFlaskStreaming($report, $filePath);
            
            return redirect()->route('reports.index')
                ->with('success', 'PDF extraction started. Refresh page to see progress.');
                
        } catch (\Exception $e) {
            $report->update([
                'status' => 'failed',
                'error_message' => $e->getMessage()
            ]);
            
            return back()->with('error', 'Extraction failed: ' . $e->getMessage());
        }
    }

    /**
     * Send PDF to Flask API with streaming (for 85-page PDFs)
     */
    private function sendToFlaskStreaming(ReportUpload $report, $filePath)
    {
        $ch = curl_init("{$this->flaskApiUrl}/api/extract/stream");
        
        // Prepare file for upload
        $cfile = new \CURLFile($filePath, 'application/pdf', $report->original_name);
        
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => ['file' => $cfile],
            CURLOPT_RETURNTRANSFER => false,
            CURLOPT_TIMEOUT => 0, // No timeout
            CURLOPT_WRITEFUNCTION => function($curl, $data) use ($report) {
                // Parse Server-Sent Events
                if (str_starts_with($data, 'data: ')) {
                    $json = trim(substr($data, 6));
                    if (!empty($json)) {
                        try {
                            $event = json_decode($json, true);
                            
                            if ($event['type'] === 'start') {
                                // Got total pages
                                $report->update(['total_pages' => $event['total_pages']]);
                                Log::info("Started extracting {$event['total_pages']} pages for report {$report->id}");
                                
                            } elseif ($event['type'] === 'page') {
                                // Save page data to database
                                $this->savePage($report, $event);
                                
                                // Update progress
                                $processed = $event['page_number'];
                                $total = $report->total_pages;
                                $report->update([
                                    'processed_pages' => $processed,
                                    'progress_percent' => round(($processed / $total) * 100, 2)
                                ]);
                                
                                Log::info("Saved page {$processed}/{$total} for report {$report->id}");
                                
                            } elseif ($event['type'] === 'complete') {
                                // All done
                                $report->update([
                                    'status' => 'completed',
                                    'progress_percent' => 100
                                ]);
                                Log::info("Completed extraction for report {$report->id}");
                                
                            } elseif ($event['type'] === 'error') {
                                throw new \Exception($event['error']);
                            }
                        } catch (\Exception $e) {
                            Log::error("Error parsing event: " . $e->getMessage());
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

    /**
     * Save one page of data to database
     */
    private function savePage(ReportUpload $report, array $pageData)
    {
        $period = $report->month ? date('F', mktime(0, 0, 0, $report->month, 1)) : 'Full Year';
        
        // Save crime statistics
        $crimeStats = $pageData['crime_statistics'] ?? [];
        foreach ($crimeStats as $stat) {
            if (empty($stat['crime_head'])) continue;
            
            // Get or create crime head
            $crimeHead = \App\Models\CrimeHead::firstOrCreate(
                ['name' => $stat['crime_head']],
                ['category' => 'Other']
            );
            
            $registered = $stat['registered'] ?? 0;
            $detected = $stat['detected'] ?? 0;
            $detectionPercent = $registered > 0 ? round(($detected / $registered) * 100, 2) : 0;
            
            // Create crime statistic record
            \App\Models\CrimeStatistic::create([
                'report_upload_id' => $report->id,
                'crime_head_id' => $crimeHead->id,
                'year' => $report->year,
                'period' => $period,
                'registered' => $registered,
                'detected' => $detected,
                'detection_percent' => $detectionPercent,
                'page_number' => $pageData['page_number'] ?? 0
            ]);
            
            // Create pending cases record
            \App\Models\PendingCasesByHead::create([
                'report_upload_id' => $report->id,
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
            
            \App\Models\ConvictionStats::create([
                'report_upload_id' => $report->id,
                'year' => $report->year,
                'decided' => $decided,
                'convicted' => $convicted,
                'acquitted' => $acquitted,
                'conviction_percent' => $convictionPercent,
                'page_number' => $pageData['page_number'] ?? 0
            ]);
        }
    }

    /**
     * Get extraction progress (for AJAX polling)
     */
    public function getProgress(ReportUpload $report)
    {
        return response()->json([
            'status' => $report->status,
            'total_pages' => $report->total_pages,
            'processed_pages' => $report->processed_pages,
            'progress_percent' => $report->progress_percent,
            'error_message' => $report->error_message
        ]);
    }
}
```

---

## Step 4: Add Columns to report_uploads Table

```php
<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up()
    {
        Schema::table('report_uploads', function (Blueprint $table) {
            // If these columns don't exist, add them
            if (!Schema::hasColumn('report_uploads', 'status')) {
                $table->string('status')->default('pending'); // pending, processing, completed, failed
            }
            if (!Schema::hasColumn('report_uploads', 'total_pages')) {
                $table->integer('total_pages')->default(0);
            }
            if (!Schema::hasColumn('report_uploads', 'processed_pages')) {
                $table->integer('processed_pages')->default(0);
            }
            if (!Schema::hasColumn('report_uploads', 'progress_percent')) {
                $table->decimal('progress_percent', 5, 2)->default(0);
            }
            if (!Schema::hasColumn('report_uploads', 'error_message')) {
                $table->text('error_message')->nullable();
            }
        });
    }

    public function down()
    {
        Schema::table('report_uploads', function (Blueprint $table) {
            $table->dropColumn(['status', 'total_pages', 'processed_pages', 'progress_percent', 'error_message']);
        });
    }
};
```

Run migration:
```bash
php artisan migrate
```

---

## Step 5: Update .env File

```bash
# Add this to your Laravel .env file
FLASK_API_URL=https://your-railway-app.railway.app
```

**For local testing:**
```bash
FLASK_API_URL=http://localhost:5000
```

---

## How It Works

1. **User clicks "Extract Data" button**
2. **Laravel sends POST** to `/reports/{id}/extract`
3. **Controller gets PDF file path** from database
4. **Laravel sends PDF** to Flask API at `https://your-flask-app.railway.app/api/extract/stream`
5. **Flask processes page-by-page**, sends results back in real-time
6. **Laravel saves each page** to database as it arrives
7. **Progress updates** automatically (processed_pages, progress_percent)
8. **When done**, status changes to "completed"

---

## Testing

1. Upload a PDF manually to `storage/app/reports/test.pdf`
2. Create a record in `report_uploads` table:
```sql
INSERT INTO report_uploads (file_path, original_name, year, month, status) 
VALUES ('reports/test.pdf', 'test.pdf', 2024, 9, 'pending');
```
3. Click "Extract Data" button
4. Watch the database - `processed_pages` will increase as pages are processed!

---

## Optional: Real-Time Progress with JavaScript

Add this to your view to show live progress without page refresh:

```javascript
<script>
function pollProgress(reportId) {
    const interval = setInterval(async () => {
        const response = await fetch(`/api/reports/${reportId}/progress`);
        const data = await response.json();
        
        // Update progress bar
        const progressBar = document.getElementById(`progress-${reportId}`);
        if (progressBar) {
            progressBar.style.width = data.progress_percent + '%';
            progressBar.textContent = `${data.processed_pages}/${data.total_pages} pages`;
        }
        
        // Stop polling when complete
        if (data.status === 'completed' || data.status === 'failed') {
            clearInterval(interval);
            location.reload(); // Refresh page
        }
    }, 2000); // Check every 2 seconds
}

// Start polling when page loads for processing reports
@foreach($reports as $report)
    @if($report->status == 'processing')
        pollProgress({{ $report->id }});
    @endif
@endforeach
</script>
```

---

## Summary for Your Laravel Developer

**What to do:**

1. ✅ Copy the controller code above
2. ✅ Add the route
3. ✅ Run the migration to add progress columns
4. ✅ Add `FLASK_API_URL` to `.env`
5. ✅ Add "Extract Data" button to your reports list
6. ✅ Test with a PDF!

**What the API needs:**
- Just the PDF file sent as `multipart/form-data` with field name `file`
- That's it! The Flask API handles everything else

**What you get back:**
- Real-time page-by-page processing
- Progress updates in database
- Crime statistics saved automatically
- No timeout issues even for 85-page PDFs! 🚀
