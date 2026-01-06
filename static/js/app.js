const API_BASE_URL = window.location.origin;

// Elements
const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');
const fileInfo = document.getElementById('fileInfo');
const fileName = document.getElementById('fileName');
const extractBtn = document.getElementById('extractBtn');
const refreshBtn = document.getElementById('refreshBtn');
const pdfList = document.getElementById('pdfList');
const results = document.getElementById('results');
const loadingSpinner = document.getElementById('loadingSpinner');

let selectedFile = null;

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    loadPDFList();
});

// Upload Area Events
uploadArea.addEventListener('click', () => fileInput.click());

uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.classList.add('dragover');
});

uploadArea.addEventListener('dragleave', () => {
    uploadArea.classList.remove('dragover');
});

uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('dragover');
    
    const files = e.dataTransfer.files;
    if (files.length > 0 && files[0].type === 'application/pdf') {
        handleFileSelect(files[0]);
    } else {
        showError('Please upload a PDF file');
    }
});

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFileSelect(e.target.files[0]);
    }
});

// Handle File Selection
function handleFileSelect(file) {
    selectedFile = file;
    fileName.textContent = file.name;
    fileInfo.style.display = 'block';
}

// Extract PDF
extractBtn.addEventListener('click', async () => {
    if (!selectedFile) return;
    
    const formData = new FormData();
    formData.append('file', selectedFile);
    
    try {
        // Show loading
        results.innerHTML = '';
        loadingSpinner.style.display = 'block';
        extractBtn.disabled = true;
        extractBtn.textContent = 'Processing...';
        
        // Upload and extract
        const response = await fetch(`${API_BASE_URL}/extract`, {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        // Hide loading
        loadingSpinner.style.display = 'none';
        extractBtn.disabled = false;
        extractBtn.textContent = 'Extract Content';
        
        if (data.success) {
            displayResults(data);
            loadPDFList(); // Refresh the list
        } else {
            showError(data.error || 'Extraction failed');
        }
        
    } catch (error) {
        loadingSpinner.style.display = 'none';
        extractBtn.disabled = false;
        extractBtn.textContent = 'Extract Content';
        showError(`Error: ${error.message}`);
    }
});

// Load PDF List
async function loadPDFList() {
    try {
        const response = await fetch(`${API_BASE_URL}/list-pdfs`);
        const data = await response.json();
        
        if (data.pdfs && data.pdfs.length > 0) {
            displayPDFList(data.pdfs);
        } else {
            pdfList.innerHTML = '<p class="placeholder">No PDFs uploaded yet</p>';
        }
    } catch (error) {
        console.error('Error loading PDF list:', error);
    }
}

// Display PDF List
function displayPDFList(pdfs) {
    pdfList.innerHTML = pdfs.map(pdf => `
        <div class="pdf-item">
            <span class="pdf-item-name">📄 ${pdf.name}</span>
            <span class="pdf-item-size">${formatBytes(pdf.size)}</span>
            <button class="btn btn-primary btn-small" onclick="extractExistingPDF('${pdf.name}')">
                Extract
            </button>
        </div>
    `).join('');
}

// Extract Existing PDF
async function extractExistingPDF(pdfName) {
    try {
        results.innerHTML = '';
        loadingSpinner.style.display = 'block';
        
        const response = await fetch(`${API_BASE_URL}/extract-saved/${encodeURIComponent(pdfName)}`, {
            method: 'POST'
        });
        
        const data = await response.json();
        
        loadingSpinner.style.display = 'none';
        
        if (data.success) {
            displayResults(data);
        } else {
            showError(data.error || 'Extraction failed');
        }
        
    } catch (error) {
        loadingSpinner.style.display = 'none';
        showError(`Error: ${error.message}`);
    }
}

// Display Results
function displayResults(data) {
    const successPages = data.data.filter(page => !page.error).length;
    const failedPages = data.total_pages - successPages;
    
    let html = `
        <div class="result-header">
            <h3>✅ Extraction Complete: ${data.filename}</h3>
            <button class="btn btn-primary btn-small" onclick="downloadJSON()">
                Download JSON
            </button>
        </div>
        
        <div class="result-stats">
            <div class="stat-item">
                <div class="stat-value">${data.total_pages}</div>
                <div class="stat-label">Total Pages</div>
            </div>
            <div class="stat-item">
                <div class="stat-value">${successPages}</div>
                <div class="stat-label">Success</div>
            </div>
            <div class="stat-item">
                <div class="stat-value">${failedPages}</div>
                <div class="stat-label">Failed</div>
            </div>
        </div>
        
        <h4 style="margin-bottom: 1rem; color: #333;">Extracted Data (JSON):</h4>
        <div class="json-viewer">
            <pre>${syntaxHighlight(JSON.stringify(data.data, null, 2))}</pre>
        </div>
    `;
    
    results.innerHTML = html;
    
    // Store for download
    window.lastExtraction = data;
}

// Download JSON
function downloadJSON() {
    if (!window.lastExtraction) return;
    
    const dataStr = JSON.stringify(window.lastExtraction.data, null, 2);
    const blob = new Blob([dataStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    
    const a = document.createElement('a');
    a.href = url;
    a.download = `${window.lastExtraction.filename.replace('.pdf', '')}_extracted.json`;
    a.click();
    
    URL.revokeObjectURL(url);
}

// Show Error
function showError(message) {
    results.innerHTML = `
        <div class="error">
            <div class="error-title">❌ Error</div>
            <div>${message}</div>
        </div>
    `;
}

// Refresh Button
refreshBtn.addEventListener('click', loadPDFList);

// Utilities
function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

function syntaxHighlight(json) {
    json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
        let cls = 'number';
        if (/^"/.test(match)) {
            if (/:$/.test(match)) {
                cls = 'key';
                return '<span style="color: #9cdcfe;">' + match + '</span>';
            } else {
                cls = 'string';
                return '<span style="color: #ce9178;">' + match + '</span>';
            }
        } else if (/true|false/.test(match)) {
            cls = 'boolean';
            return '<span style="color: #569cd6;">' + match + '</span>';
        } else if (/null/.test(match)) {
            cls = 'null';
            return '<span style="color: #569cd6;">' + match + '</span>';
        }
        return '<span style="color: #b5cea8;">' + match + '</span>';
    });
}
