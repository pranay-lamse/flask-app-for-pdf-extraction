# 🎨 Laravel UI Guide: Handling Long Python Processes

You are absolutely right! Since PDF extraction can take 2-5 minutes, we **cannot** just let the user wait on a blank loading screen.

Here is the correct strategy to handle this "Waiting Time" so it feels fast and smooth.

## ❌ The Wrong Way (Synchronous)
1. User clicks "Extract".
2. Browser spins for 5 minutes 🔄.
3. User thinks it crashed.
4. Browser gives up (Timeout Error).

## ✅ The Right Way (Streaming / Real-Time)
1. User clicks "Extract".
2. **Immediate** feedback: "Started processing..."
3. A **Progress Bar** appears.
4. As Python finishes Page 1, the bar moves: "Processed 1/85 pages".
5. User sees constant activity.

---

## 💻 Frontend Implementation (Copy-Paste)

Add this simple **JavaScript** to your Laravel Blade View (`resources/views/reports/show.blade.php`).

### 1. The HTML (Progress Bar)
```html
<div class="card p-4">
    <h3>📄 PDF Extraction</h3>
    
    <!-- The Extract Button -->
    <button id="extractBtn" class="btn btn-primary" onclick="startExtraction()">
        Start Extraction
    </button>

    <!-- Progress Container (Hidden by default) -->
    <div id="progressContainer" style="display:none; margin-top: 20px;">
        <p>Processing Status: <strong id="statusText">Connecting...</strong></p>
        
        <div class="progress" style="height: 25px;">
            <div id="progressBar" class="progress-bar progress-bar-striped progress-bar-animated" 
                 role="progressbar" style="width: 0%">0%</div>
        </div>
        
        <!-- Live Log Area -->
        <div id="logs" style="height: 150px; overflow-y: scroll; background: #f8f9fa; border: 1px solid #ddd; padding: 10px; margin-top: 10px; font-family: monospace; font-size: 12px;">
            <div class="text-muted">Ready to start...</div>
        </div>
    </div>
</div>
```

### 2. The JavaScript (The Magic)
This script talks to your Flask API using `EventSource`. This is what allows it to update **live** without waiting for the whole file.

```javascript
<script>
function startExtraction() {
    // 1. UI Updates
    document.getElementById('extractBtn').disabled = true;
    document.getElementById('extractBtn').innerText = 'Processing...';
    document.getElementById('progressContainer').style.display = 'block';
    
    // 2. Setup Server-Sent Events (SSE) connection
    const pdfId = "{{ $report->id }}"; // Get ID from Laravel
    
    // NOTE: Replace with your actual Flask API URL
    const apiUrl = "https://your-flask-app.railway.app/api/extract/stream"; 
    
    // We need to send the file via POST, but EventSource is GET-only.
    // Ideally, Laravel acts as a proxy, or we use 'fetch' to POST and read the stream.
    // For simplicity, here is the 'fetch' streaming approach (Modern JS):
    
    const formData = new FormData();
    const fileInput = document.getElementById('fileInput'); // Assume you have a file input
    formData.append('file', fileInput.files[0]);

    fetch(apiUrl, {
        method: 'POST',
        body: formData
    }).then(async response => {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value);
            const lines = chunk.split('\n');
            
            lines.forEach(line => {
                if (line.startsWith('data: ')) {
                    const jsonStr = line.replace('data: ', '');
                    try {
                        const data = JSON.parse(jsonStr);
                        handleServerMessage(data);
                    } catch (e) {
                         // Ignore incomplete chunks
                    }
                }
            });
        }
    }).catch(err => {
        logError("Connection failed: " + err);
    });
}

function handleServerMessage(data) {
    const logs = document.getElementById('logs');
    const progressBar = document.getElementById('progressBar');
    const statusText = document.getElementById('statusText');

    if (data.type === 'start') {
        logMessage(`🚀 Started: ${data.filename} (${data.total_pages} pages)`);
        window.totalPages = data.total_pages;
    } 
    else if (data.type === 'page') {
        // Calculate progress
        const percent = Math.round((data.page_number / window.totalPages) * 100);
        
        // Update UI
        progressBar.style.width = percent + '%';
        progressBar.innerText = percent + '%';
        statusText.innerText = `Processing Page ${data.page_number} of ${window.totalPages}`;
        
        logMessage(`✅ Page ${data.page_number} extracted & saved.`);
    } 
    else if (data.type === 'complete') {
        progressBar.classList.remove('progress-bar-animated');
        progressBar.classList.add('bg-success');
        statusText.innerText = "Completed Successfully!";
        document.getElementById('extractBtn').innerText = 'Done';
        logMessage("✨ All finished!");
    }
    else if (data.type === 'error') {
        progressBar.classList.add('bg-danger');
        statusText.innerText = "Error: " + data.error;
        logMessage("❌ Error: " + data.error);
    }
}

function logMessage(msg) {
    const logs = document.getElementById('logs');
    const div = document.createElement('div');
    div.innerText = new Date().toLocaleTimeString() + " - " + msg;
    logs.prepend(div);
}
</script>
```

## 🧠 What's happening here?
1. **User Clicks**: Interface immediately shows "Processing..." (Instant feedback).
2. **Fetch Stream**: We open a continuous connection to Python.
3. **While Loop**: Data arrives piece by piece.
   - We get "Page 1"... JavaScript updates the bar to 1%.
   - We get "Page 2"... JavaScript updates the bar to 3%.
4. **Completion**: When Python says "Done", valid indicators turn green.

This ensures the user **never** feels like the app is "stuck", even if it takes 10 minutes.
