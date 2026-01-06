document.addEventListener('DOMContentLoaded', () => {
    loadDashboardData();
});

let charts = {};

async function loadDashboardData() {
    const loadingState = document.getElementById('loadingState');
    const dashboardContent = document.getElementById('dashboardContent');
    const errorMessage = document.getElementById('errorMessage');
    const reportTitle = document.getElementById('reportTitle');

    try {
        const response = await fetch('/api/latest-report-data');
        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Failed to fetch data');
        }

        // Update Title
        if (result.year) {
            reportTitle.textContent = `Report Analysis: ${result.month || ''} ${result.year}`;
        }

        // Process Data
        let processedData = [{
            crime_statistics: result.crime_statistics,
            conviction_stats: result.conviction_stats || {}
        }];

        // Merge Pending Stats
        if (result.pending_by_head) {
            result.pending_by_head.forEach(p => {
                const match = processedData[0].crime_statistics.find(c => c.crime_head === p.crime_head);
                if (match) {
                    match.pending_0_3 = p.pending_0_3;
                    match.pending_3_6 = p.pending_3_6;
                    match.pending_6_12 = p.pending_6_12;
                    match.pending_1_year = p.pending_1_year;
                } else {
                    processedData[0].crime_statistics.push({
                        crime_head: p.crime_head,
                        registered: 0, detected: 0,
                        pending_0_3: p.pending_0_3,
                        pending_3_6: p.pending_3_6,
                        pending_6_12: p.pending_6_12,
                        pending_1_year: p.pending_1_year
                    });
                }
            });
        }

        renderCharts(processedData);

        loadingState.classList.add('hidden');
        dashboardContent.classList.remove('hidden');

    } catch (error) {
        loadingState.classList.add('hidden');
        errorMessage.textContent = `Error: ${error.message}`;
        errorMessage.classList.remove('hidden');
    }
}

function renderCharts(data) {
    // Destroy existing charts
    Object.values(charts).forEach(chart => chart.destroy());

    // Aggregate Data
    let crimeStats = {};
    let pendingStats = {
        '0-3 Months': 0, '3-6 Months': 0, '6-12 Months': 0, '> 1 Year': 0
    };
    let convictionStats = { decided: 0, convicted: 0, acquitted: 0 };

    data.forEach(page => {
        const pageStats = page.crime_statistics || [];
        pageStats.forEach(stat => {
            const head = stat.crime_head || 'Unknown';
            if (!crimeStats[head]) {
                crimeStats[head] = { registered: 0, detected: 0 };
            }
            crimeStats[head].registered += parseInt(stat.registered || 0);
            crimeStats[head].detected += parseInt(stat.detected || 0);

            // Pending
            pendingStats['0-3 Months'] += parseInt(stat.pending_0_3 || 0);
            pendingStats['3-6 Months'] += parseInt(stat.pending_3_6 || 0);
            pendingStats['6-12 Months'] += parseInt(stat.pending_6_12 || 0);
            pendingStats['> 1 Year'] += parseInt(stat.pending_1_year || 0);
        });

        if (page.conviction_stats) {
            convictionStats.decided += parseInt(page.conviction_stats.decided || 0);
            convictionStats.convicted += parseInt(page.conviction_stats.convicted || 0);
            convictionStats.acquitted += parseInt(page.conviction_stats.acquitted || 0);
        }
    });

    const labels = Object.keys(crimeStats);
    const registeredData = labels.map(l => crimeStats[l].registered);
    const detectedData = labels.map(l => crimeStats[l].detected);
    const detectionRates = labels.map(l => {
        const reg = crimeStats[l].registered;
        return reg > 0 ? ((crimeStats[l].detected / reg) * 100).toFixed(1) : 0;
    });

    // Theme Colors (Matching CSS Light Theme)
    const colors = {
        primary: '#4f46e5',   // Indigo 600
        success: '#059669',   // Emerald 600
        danger: '#dc2626',    // Rose 600
        warning: '#d97706',   // Amber 600
        info: '#2563eb',      // Blue 600
        text: '#64748b',      // Slate 500 (Secondary Text)
        bg: '#ffffff',        // White
        grid: '#e2e8f0'       // Slate 200
    };

    const commonOptions = {
        responsive: true,
        plugins: {
            legend: { labels: { color: colors.text } }
        },
        scales: {
            x: {
                grid: { color: colors.grid },
                ticks: { color: colors.text }
            },
            y: {
                beginAtZero: true,
                grid: { color: colors.grid },
                ticks: { color: colors.text }
            }
        }
    };

    // 1. Crime Overview Chart
    charts.crime = new Chart(document.getElementById('crimeStatsChart'), {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Registered',
                    data: registeredData,
                    backgroundColor: 'rgba(239, 68, 68, 0.5)', // Rose transparent
                    borderColor: colors.danger,
                    borderWidth: 1,
                    borderRadius: 4
                },
                {
                    label: 'Detected',
                    data: detectedData,
                    backgroundColor: 'rgba(16, 185, 129, 0.5)', // Emerald transparent
                    borderColor: colors.success,
                    borderWidth: 1,
                    borderRadius: 4
                }
            ]
        },
        options: commonOptions
    });

    // 2. Detection Efficiency
    charts.detection = new Chart(document.getElementById('detectionChart'), {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Detection Rate (%)',
                data: detectionRates,
                borderColor: colors.info,
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                fill: true,
                tension: 0.4,
                pointBackgroundColor: colors.bg
            }]
        },
        options: {
            ...commonOptions,
            scales: {
                ...commonOptions.scales,
                y: { ...commonOptions.scales.y, max: 100 }
            }
        }
    });

    // 3. Conviction
    charts.conviction = new Chart(document.getElementById('convictionChart'), {
        type: 'doughnut',
        data: {
            labels: ['Convicted', 'Acquitted', 'Pending Decision'],
            datasets: [{
                data: [
                    convictionStats.convicted,
                    convictionStats.acquitted,
                    convictionStats.decided - (convictionStats.convicted + convictionStats.acquitted)
                ],
                backgroundColor: [colors.success, colors.danger, colors.warning],
                borderWidth: 0,
                hoverOffset: 4
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'right', labels: { color: colors.text } }
            }
        }
    });

    // 4. Pending Cases
    charts.pending = new Chart(document.getElementById('pendingCasesChart'), {
        type: 'bar',
        data: {
            labels: Object.keys(pendingStats),
            datasets: [{
                label: 'Pending Cases',
                data: Object.values(pendingStats),
                backgroundColor: 'rgba(99, 102, 241, 0.6)', // Indigo transparent
                borderColor: colors.primary,
                borderWidth: 1,
                borderRadius: 6
            }]
        },
        options: {
            ...commonOptions,
            plugins: { legend: { display: false } }
        }
    });
}
