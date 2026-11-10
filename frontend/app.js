const API_BASE = 'http://localhost:8000';

// Utility: format date/time in China Standard Time (Beijing)
// Robust across browsers (incl. Safari) and formats consistently as YYYY-MM-DD HH:mm:ss CST
function formatBeijing(dateInput) {
    if (!dateInput) return '-';
    let d;
    if (dateInput instanceof Date) {
        d = dateInput;
    } else if (typeof dateInput === 'string') {
        // Normalize microseconds -> milliseconds for Safari
        const sanitized = dateInput.replace(/(\.\d{3})\d+/, '$1');
        d = new Date(sanitized);
        if (isNaN(d.getTime())) d = new Date(dateInput.replace(/\.\d+/, ''));
    } else {
        d = new Date(dateInput);
    }
    try {
        const fmt = new Intl.DateTimeFormat('en-CA', {
            timeZone: 'Asia/Shanghai',
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
            hour12: false
        });
        const parts = fmt.formatToParts(d).reduce((acc, p) => (acc[p.type] = p.value, acc), {});
        return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} CST`;
    } catch (_) {
        // Fallback if Intl/timeZone unsupported
        return d.toLocaleString('en-CA') + ' CST';
    }
}

function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function statusChip(status) {
    if (!status) return '';
    const norm = status.toLowerCase().replace(/[^a-z0-9]/g, '_');
    return `<span class="status-chip status-${norm}">${escapeHtml(status)}</span>`;
}

function isFiniteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value);
}

// Switch tabs
function switchTab(evt, tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    if (evt && evt.currentTarget) {
        evt.currentTarget.classList.add('active');
    }
    document.getElementById(tabName).classList.add('active');
    
    // Load data when switching tabs
    if (tabName === 'activity') loadActivity();
    if (tabName === 'portfolio') {
        loadPortfolio();
        // Duplicate content to portfolio tab
        document.getElementById('equityChangesPortfolio').innerHTML = document.getElementById('equityChanges').innerHTML;
        document.getElementById('exchangeHoldingsPortfolio').innerHTML = document.getElementById('exchangeHoldings').innerHTML;
    }
}

// Check backend status
async function checkStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/config`);
        const config = await res.json();
        document.getElementById('backendStatus').textContent = 'Connected';
        document.getElementById('statusDot').style.background = '#4ade80';
        document.getElementById('modeStatus').textContent = config.mode;
        document.getElementById('cadenceStatus').textContent = `${config.ai_cadence_minutes}m`;
        document.getElementById('lastUpdate').textContent = formatBeijing(new Date());
        
        // Update pause status
        const isPaused = config.paused || false;
        document.getElementById('pauseStatus').textContent = isPaused ? 'Paused' : 'Running';
        document.getElementById('pauseStatusText').style.color = isPaused ? '#d97706' : '#16a34a';
        document.getElementById('pauseBtnText').textContent = isPaused ? '▶️ Resume' : '⏸️ Pause';
    } catch (err) {
        document.getElementById('backendStatus').textContent = 'Offline';
        document.getElementById('statusDot').style.background = '#f87171';
    }
}

// Load config
async function loadConfig() {
    try {
        const res = await fetch(`${API_BASE}/api/config`);
        const config = await res.json();
        
        // Mode is read-only from .env, no need to set in UI
        document.getElementById('ai_cadence_minutes').value = config.ai_cadence_minutes;
        document.getElementById('price_spike_trigger_pct').value = config.price_spike_trigger_pct;
        document.getElementById('margin_enabled').checked = !!config.margin_enabled;
        document.getElementById('max_leverage').value = config.max_leverage ?? '';
        document.getElementById('max_position_usd').value = config.max_position_usd ?? 100;
        document.getElementById('max_total_exposure_usd').value = config.max_total_exposure_usd ?? 200;
        document.getElementById('daily_loss_cap_usd').value = config.daily_loss_cap_usd ?? 50;
        document.getElementById('symbols').value = (config.symbols || []).join(', ');
        document.getElementById('strategist_cache_ttl_minutes').value = config.strategist_cache_ttl_minutes ?? 240;
        document.getElementById('tactician_hold_retry_minutes').value = config.tactician_hold_retry_minutes ?? 30;
        document.getElementById('executor_hold_retry_minutes').value = config.executor_hold_retry_minutes ?? 5;
        document.getElementById('executor_deviation_threshold_pct').value = config.executor_deviation_threshold_pct ?? 1.5;
        document.getElementById('plan_execution_timeout_minutes').value = config.plan_execution_timeout_minutes ?? 15;
        document.getElementById('max_symbol_workers').value = config.max_symbol_workers ?? 4;
        document.getElementById('strategist_provider').value = (config.strategist_provider || 'deepseek');
        document.getElementById('strategist_model').value = config.strategist_model ?? '';
        document.getElementById('tactician_provider').value = (config.tactician_provider || 'deepseek');
        document.getElementById('tactician_model').value = config.tactician_model ?? '';
        document.getElementById('executor_provider').value = (config.executor_provider || 'deepseek');
        document.getElementById('executor_model').value = config.executor_model ?? '';
        
        console.info('Config loaded');
    } catch (err) {
        alert('❌ Failed to load config: ' + err.message);
    }
}

// Save config
async function saveConfig() {
    const config = {
        ai_cadence_minutes: parseInt(document.getElementById('ai_cadence_minutes').value),
        price_spike_trigger_pct: parseFloat(document.getElementById('price_spike_trigger_pct').value),
        margin_enabled: document.getElementById('margin_enabled').checked,
        max_leverage: parseInt(document.getElementById('max_leverage').value),
        max_position_usd: parseFloat(document.getElementById('max_position_usd').value),
        max_total_exposure_usd: parseFloat(document.getElementById('max_total_exposure_usd').value),
        daily_loss_cap_usd: parseFloat(document.getElementById('daily_loss_cap_usd').value),
        strategist_cache_ttl_minutes: parseInt(document.getElementById('strategist_cache_ttl_minutes').value),
        tactician_hold_retry_minutes: parseInt(document.getElementById('tactician_hold_retry_minutes').value),
        executor_hold_retry_minutes: parseInt(document.getElementById('executor_hold_retry_minutes').value),
        executor_deviation_threshold_pct: parseFloat(document.getElementById('executor_deviation_threshold_pct').value),
        plan_execution_timeout_minutes: parseInt(document.getElementById('plan_execution_timeout_minutes').value),
        max_symbol_workers: parseInt(document.getElementById('max_symbol_workers').value),
        strategist_provider: document.getElementById('strategist_provider').value,
        strategist_model: document.getElementById('strategist_model').value.trim() || null,
        tactician_provider: document.getElementById('tactician_provider').value,
        tactician_model: document.getElementById('tactician_model').value.trim() || null,
        executor_provider: document.getElementById('executor_provider').value,
        executor_model: document.getElementById('executor_model').value.trim() || null,
    };

    const symbolsRaw = document.getElementById('symbols').value || '';
    const parsedSymbols = symbolsRaw.split(',').map(s => s.trim()).filter(Boolean);
    if (parsedSymbols.length > 0) {
        config.symbols = parsedSymbols;
    }
    
    try {
        const res = await fetch(`${API_BASE}/api/config`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(config)
        });
        const result = await res.json();
        alert('✅ Config saved successfully');
        checkStatus();
    } catch (err) {
        alert('❌ Failed to save config: ' + err.message);
    }
}

// Trigger AI decision
async function triggerAgent(agent) {
    try {
        const endpoint = agent === 'strategist' 
            ? '/api/decision/run-strategist'
            : agent === 'tactician'
            ? '/api/decision/run-tactician'
            : '/api/decision/run-executor';
        
        const res = await fetch(`${API_BASE}${endpoint}`, {method: 'POST'});
        const result = await res.json();
        
        const agentNames = {strategist: '🧠 Strategist', tactician: '📋 Tactician', executor: '⚡ Executor'};
        const agentName = agentNames[agent];
        
        const successCount = result.results.filter(r => r.status === 'success').length;
        const totalCount = result.results.length;
        
        alert(`✅ ${agentName} completed: ${successCount}/${totalCount} symbols successful`);
        
        // Show any errors
        const errors = result.results.filter(r => r.status === 'error');
        if (errors.length > 0) {
            console.log(`${agentName} errors:`, errors);
        }
        
        loadDecisions();
        loadActivity();
    } catch (err) {
        alert('❌ Failed to trigger agent: ' + err.message);
    }
}

async function triggerAI() {
    try {
        const res = await fetch(`${API_BASE}/api/decision/run`, {method: 'POST'});
        const result = await res.json();
        alert('✅ AI decision triggered. Check Decisions tab.');
        loadDecisions();
        loadActivity();
    } catch (err) {
        alert('❌ Failed to trigger AI: ' + err.message);
    }
}

// Toggle pause
async function togglePause() {
    try {
        const currentConfig = await fetch(`${API_BASE}/api/config`).then(r => r.json());
        const newPaused = !currentConfig.paused;
        
        const res = await fetch(`${API_BASE}/api/config`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ paused: newPaused })
        });
        
        if (res.ok) {
            checkStatus();
            alert(newPaused ? '⏸️ System paused' : '▶️ System resumed');
        }
    } catch (err) {
        alert('❌ Failed to toggle pause: ' + err.message);
    }
}

// Pagination state for decisions
let decisionsPage = 1;
let decisionsPageSize = 10;
let decisionsTotal = 0;
let decisionsHasMore = false;

// Load decisions
async function loadDecisions(page = decisionsPage) {
    try {
        decisionsPage = page;
        const res = await fetch(`${API_BASE}/api/export/decisions?format=json&page=${decisionsPage}&page_size=${decisionsPageSize}`);
        const data = await res.json();
        decisionsTotal = data.total ?? 0;
        const pageCount = data.count ?? (data.data ? data.data.length : 0);
        // hasMore: prefer total when present; fallback to page size heuristic
        decisionsHasMore = decisionsTotal
            ? decisionsPage * decisionsPageSize < decisionsTotal
            : (pageCount === decisionsPageSize);
        const container = document.getElementById('decisionsContainer');
        if (!data.data || data.data.length === 0) {
            container.innerHTML = '<p style="color: #6b7280;">No decisions yet</p>';
            updateDecisionsPager(0);
            return;
        }
        
        container.innerHTML = data.data.map(d => {
            const output = d.ai_output || {};
            const actions = output.actions || [];
            const ts = d.timestamp_ms ? formatBeijing(new Date(d.timestamp_ms)) : formatBeijing(d.timestamp);
            
            return `
                <div class="decision-card">
                    <div class="decision-header">
                        <span style="color: #6b7280; font-size: 14px;">${ts}</span>
                        <span style="font-size: 14px;">Confidence: <strong>${(output.confidence * 100).toFixed(0)}%</strong></span>
                    </div>
                    <div class="confidence-bar">
                        <div class="confidence-fill" style="width: ${output.confidence * 100}%"></div>
                    </div>
                    <div style="margin: 10px 0;">
                        ${actions.map(a => {
                            const price = (d.ai_input?.market?.[a.symbol]?.last_price || 0).toFixed(2);
                            const sizeDisplay = a.size_usd 
                                ? `$${a.size_usd.toFixed(2)}` 
                                : (a.size_pct ? `${a.size_pct}%` : 'N/A');
                            
                            // Find execution result for this action
                            const execResult = d.execution_results?.find(e => 
                                e.symbol === a.symbol && e.action === a.action
                            );
                            
                            return `
                                <div style="margin: 5px 0;">
                                    <span class="action-badge action-${a.action}">${a.symbol}: ${a.action.toUpperCase()}</span>
                                    ${a.action !== 'hold' ? `<span style="color: #6b7280; font-size: 12px; margin-left: 8px;">@ $${price} (${sizeDisplay})</span>` : ''}
                                    ${execResult ? `
                                        <span class="badge badge-${execResult.status === 'success' ? 'success' : 'danger'}" style="margin-left: 8px; font-size: 11px;">
                                            ${execResult.status === 'success' ? '✓ EXECUTED' : '✗ FAILED'}
                                        </span>
                                    ` : ''}
                                </div>
                                <div style="color: #6b7280; font-size: 12px; margin-left: 8px; font-style: italic;">${a.reason || ''}</div>
                                ${execResult && execResult.status === 'success' ? `
                                    <div style="color: #059669; font-size: 11px; margin-left: 8px; margin-top: 2px;">
                                        Order: ${execResult.order_id || 'N/A'} | Filled: ${execResult.filled_size || 0} @ $${execResult.avg_price || 0}
                                    </div>
                                ` : ''}
                                ${execResult && execResult.status === 'failed' ? `
                                    <div style="color: #dc2626; font-size: 11px; margin-left: 8px; margin-top: 2px;">
                                        Error: ${execResult.message || 'Unknown error'}
                                    </div>
                                ` : ''}
                            `;
                        }).join('')}
                    </div>
                    <div style="color: #6b7280; font-size: 13px; margin-top: 10px; padding-top: 10px; border-top: 1px solid #e5e7eb;">
                        ${output.rationale || 'No rationale provided'}
                    </div>
                </div>
            `;
        }).join('');

        updateDecisionsPager(decisionsTotal, decisionsHasMore);
    } catch (err) {
        document.getElementById('decisionsContainer').innerHTML = 
            `<p style="color: #dc2626;">Failed to load decisions: ${err.message}</p>`;
    }
}

function updateDecisionsPager(total, hasMore) {
    const totalKnown = !!total;
    const start = (decisionsPage - 1) * decisionsPageSize + 1;
    const end = totalKnown ? Math.min(decisionsPage * decisionsPageSize, total) : decisionsPage * decisionsPageSize;
    const totalPages = totalKnown ? Math.max(1, Math.ceil((total || 0) / decisionsPageSize)) : (decisionsPage + (hasMore ? 1 : 0));
    const infoTotal = totalKnown ? total : `${end}+`;
    document.getElementById('decisionsPageInfo').textContent = `Showing ${start}-${end} of ${infoTotal} • Page ${decisionsPage}` + (totalKnown ? `/${totalPages}` : '');
    document.getElementById('decisionsPrev').disabled = decisionsPage <= 1;
    document.getElementById('decisionsNext').disabled = totalKnown ? (decisionsPage >= totalPages) : !hasMore;
}

function changeDecisionsPage(delta) {
    // If total unknown, allow moving forward if hasMore is true
    if (!decisionsTotal && delta > 0 && !decisionsHasMore) return;
    const totalPages = decisionsTotal ? Math.max(1, Math.ceil(decisionsTotal / decisionsPageSize)) : Infinity;
    const next = decisionsTotal ? Math.min(Math.max(1, decisionsPage + delta), totalPages) : Math.max(1, decisionsPage + delta);
    if (next !== decisionsPage) {
        const prev = decisionsPage;
        loadDecisions(next).then(() => {
            // If we advanced but got back no data, revert
            const container = document.getElementById('decisionsContainer');
            if ((container.textContent || '').includes('No decisions yet')) {
                decisionsPage = prev;
                updateDecisionsPager(decisionsTotal, decisionsHasMore);
            }
        });
    }
}

function renderStepsTable(steps) {
    if (!steps || steps.length === 0) {
        return '<p style="color:#9ca3af;">No plan steps defined</p>';
    }
    const rows = steps.map(step => {
        const sizeUsd = isFiniteNumber(step.size_usd) ? `$${step.size_usd.toFixed(2)}` : '-';
        const sizePct = isFiniteNumber(step.size_pct) ? `${step.size_pct.toFixed(2)}%` : '-';
        const executedAt = step.executed_at ? formatBeijing(step.executed_at) : 'Pending';
        const price = isFiniteNumber(step.entry_price) ? `$${step.entry_price.toFixed(4)}` : '-';
        return `
            <tr>
                <td>${step.sequence_index ?? '-'}</td>
                <td>${escapeHtml(step.action || '-')}</td>
                <td>${escapeHtml((step.side || '').toUpperCase())}</td>
                <td>${price}</td>
                <td>${sizeUsd}</td>
                <td>${sizePct}</td>
                <td>${escapeHtml(step.status || '-')}</td>
                <td>${executedAt}</td>
            </tr>
        `;
    }).join('');
    return `
        <table class="plan-step-table">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Action</th>
                    <th>Side</th>
                    <th>Entry</th>
                    <th>Size (USD)</th>
                    <th>Size (%)</th>
                    <th>Status</th>
                    <th>Executed</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

function renderExecutionLogs(logs) {
    if (!logs || logs.length === 0) {
        return '<p style="color:#9ca3af; font-size:12px;">No executor logs yet</p>';
    }
    return logs.slice(0, 5).map(log => {
        const ts = log.created_at ? formatBeijing(log.created_at) : '-';
        const payload = log.payload ? escapeHtml(JSON.stringify(log.payload)) : '';
        const message = log.message ? escapeHtml(log.message) : '';
        return `
            <div class="log-line-small">
                <div style="display:flex; gap:8px; align-items:center;">
                    <strong>${ts}</strong>
                    ${statusChip(log.status || '')}
                </div>
                ${message ? `<div>${message}</div>` : ''}
                ${payload ? `<div style="color:#9ca3af;">${payload}</div>` : ''}
            </div>
        `;
    }).join('');
}

function renderExecutorSection(execution) {
    if (!execution || Object.keys(execution).length === 0) {
        return '<p style="color:#9ca3af;">Executor idle (no pending steps)</p>';
    }
    const statusLine = execution.status ? `<div class="log-line-small"><strong>Status:</strong> ${escapeHtml(execution.status)}</div>` : '';
    const rationaleLine = execution.rationale ? `<div class="log-line-small">${escapeHtml(execution.rationale)}</div>` : '';
    const orders = execution.orders || [];
    const ordersHtml = orders.length
        ? orders.map(order => {
            const fields = [];
            if (order.status) fields.push(`status: ${escapeHtml(order.status)}`);
            if (order.order_id) fields.push(`id: ${escapeHtml(order.order_id)}`);
            const avgPrice = Number(order.avg_price);
            if (Number.isFinite(avgPrice)) fields.push(`avg $${avgPrice.toFixed(4)}`);
            const filled = Number(order.filled_size);
            if (Number.isFinite(filled)) fields.push(`filled ${filled.toFixed(4)}`);
            const notional = Number(order.size_usd || order.notional_usdt);
            if (Number.isFinite(notional)) fields.push(`notional $${notional.toFixed(2)}`);
            const meta = fields.join(' • ');
            return `<div class="log-line-small"><strong>${escapeHtml(order.action || 'order')}</strong> ${meta ? `• ${meta}` : ''}</div>`;
        }).join('')
        : '<p style="color:#9ca3af; font-size:12px;">No orders executed this run</p>';
    return `${statusLine}${rationaleLine}${ordersHtml}`;
}

function summarizeConfidence(value) {
    if (value === null || value === undefined || Number.isNaN(value)) return 'N/A';
    const numeric = typeof value === 'number' ? value : parseFloat(value);
    if (!Number.isFinite(numeric)) return 'N/A';
    const pct = numeric > 1 ? numeric : numeric * 100;
    return `${pct.toFixed(0)}%`;
}

function setDecisionsPageSize(val) {
    decisionsPageSize = parseInt(val, 10) || 10;
    decisionsPage = 1;
    loadDecisions(1);
}

// Pagination state for AI data
let aiDataPage = 1;
let aiTotal = 0;
let aiHasMore = false;

// Load AI data
async function loadAIData(page = aiDataPage) {
    try {
        aiDataPage = page;
        const res = await fetch(`${API_BASE}/api/export/decisions?format=json&page=${aiDataPage}&page_size=1`);
        const data = await res.json();
        
        if (!data.data || data.data.length === 0) {
            document.getElementById('aiInput').innerHTML = '<pre>No data available</pre>';
            document.getElementById('aiOutput').innerHTML = '<pre>No data available</pre>';
            document.getElementById('aiPageInfo').textContent = 'Page 1';
            return;
        }
        
        const decision = data.data[0];
        document.getElementById('aiInput').innerHTML = 
            `<pre>${JSON.stringify(decision.ai_input, null, 2)}</pre>`;
        document.getElementById('aiOutput').innerHTML = 
            `<pre>${JSON.stringify(decision.ai_output, null, 2)}</pre>`;

        aiTotal = data.total ?? 0;
        const pageCount = data.count ?? (data.data ? data.data.length : 0);
        aiHasMore = aiTotal ? (aiDataPage < aiTotal) : (pageCount === 1); // page_size=1
        const totalPages = aiTotal ? Math.max(1, Math.ceil(aiTotal / 1)) : (aiDataPage + (aiHasMore ? 1 : 0));
        document.getElementById('aiPageInfo').textContent = aiTotal ? `Decision ${aiDataPage} of ${totalPages}` : `Decision ${aiDataPage}`;
        document.getElementById('aiPrev').disabled = aiDataPage <= 1;
        document.getElementById('aiNext').disabled = aiTotal ? (aiDataPage >= totalPages) : !aiHasMore;
    } catch (err) {
        document.getElementById('aiInput').innerHTML = 
            `<pre>Failed to load: ${err.message}</pre>`;
    }
}

// Load holdings (exchange + ledger)
async function loadHoldings() {
    try {
        const res = await fetch(`${API_BASE}/api/holdings`);
        const data = await res.json();
        const targetId = 'exchangeHoldingsPortfolio';

        // Exchange
        const ex = data.exchange || {};
        const exRows = (ex.positions || []).map(p => {
            const pct = (p.change_24h_pct ?? 0) * 100;
            const pctColor = pct >= 0 ? '#16a34a' : '#dc2626';
            const pctStr = isFinite(pct) ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '-';
            // Format price with intelligent decimal places: if < $0.01, show 6 decimals; else show 2
            const priceStr = p.last_price ? (p.last_price < 0.01 ? `$${p.last_price.toFixed(6)}` : `$${p.last_price.toFixed(2)}`) : '-';
            return `
            <tr>
                <td>${p.symbol}</td>
                <td style="text-align:right;">${(p.amount ?? 0).toFixed(8)}</td>
                <td style="text-align:right;">${priceStr}</td>
                <td style="text-align:right;">$${(p.value_usdt ?? 0).toFixed(2)}</td>
                <td style="text-align:right; color:${pctColor};">${pctStr}</td>
            </tr>`;
        }).join('') || '<tr><td colspan="5" style="color:#9ca3af;">No positions</td></tr>';
        document.getElementById(targetId).innerHTML = `
            <table style="width:100%; border-collapse: collapse;">
                <thead>
                    <tr style="text-align:left; border-bottom:1px solid #e5e7eb;">
                        <th>Symbol</th><th style="text-align:right;">Amount</th><th style="text-align:right;">Last Price</th><th style="text-align:right;">Value (USDT)</th><th style="text-align:right;">24h Change</th>
                    </tr>
                </thead>
                <tbody>${exRows}</tbody>
                <tfoot>
                    <tr style="border-top:1px solid #e5e7eb;"><td colspan="4" style="text-align:right; color:#6b7280;">Total</td><td style="text-align:right;"><strong>$${(ex.total_value_usdt ?? 0).toFixed(2)}</strong></td></tr>
                </tfoot>
            </table>`;

        // No strategy ledger section
    } catch (err) {
        document.getElementById('exchangeHoldingsPortfolio').innerHTML = `<p style="color:#dc2626;">Failed to load: ${err.message}</p>`;
    }
}

// Load equity changes
async function loadEquityChanges() {
    try {
        const res = await fetch(`${API_BASE}/api/equity/changes`);
        const targetId = 'equityChangesPortfolio';
        
        // If 404, it means no snapshots yet - show empty state
        if (res.status === 404) {
            document.getElementById(targetId).innerHTML = `
                <div class="empty-state" style="padding: 20px; text-align: center; color: #9ca3af;">
                    <div style="font-size: 24px; margin-bottom: 8px;">📊</div>
                    <div style="font-size: 14px;">No equity snapshots yet</div>
                    <div style="font-size: 12px; margin-top: 4px;">Start trading to begin tracking portfolio changes</div>
                </div>
            `;
            return;
        }
        
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        
        const data = await res.json();

        const current = data.current || {};
        const change24h = data.change_24h || {};
        const changeTotal = data.change_from_beginning || {};

        let html = '<div class="equity-summary">';

        // Current Value Box
        html += `
            <div class="equity-box">
                <h3>Current Portfolio Value</h3>
                <div class="equity-value">$${(current.value_usdt || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                <div class="equity-meta">
                    Source: ${current.source || 'N/A'} | Mode: ${current.mode || 'N/A'}
                </div>
            </div>
        `;

        // 24h Change Box
        if (change24h.available) {
            const change = change24h.change_usdt || 0;
            const changePct = change24h.change_pct || 0;
            const changeClass = change > 0 ? 'positive' : change < 0 ? 'negative' : 'neutral';
            const arrow = change > 0 ? '↑' : change < 0 ? '↓' : '→';
            
            html += `
                <div class="equity-box">
                    <h3>24 Hour Change</h3>
                    <div class="equity-change ${changeClass}">
                        <span>${arrow}</span>
                        <span>$${Math.abs(change).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
                        <span>(${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%)</span>
                    </div>
                    <div class="equity-meta">
                        From: $${(change24h.value_usdt || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}
                    </div>
                </div>
            `;
        } else {
            html += `
                <div class="equity-box">
                    <h3>24 Hour Change</h3>
                    <div class="equity-unavailable">Not available yet</div>
                    <div class="equity-meta">
                        Requires 24 hours of data
                    </div>
                </div>
            `;
        }

        // Total Change Box
        const totalChange = changeTotal.change_usdt || 0;
        const totalChangePct = changeTotal.change_pct || 0;
        const totalChangeClass = totalChange > 0 ? 'positive' : totalChange < 0 ? 'negative' : 'neutral';
        const totalArrow = totalChange > 0 ? '↑' : totalChange < 0 ? '↓' : '→';
        const durationHours = changeTotal.duration_hours || 0;
        
        html += `
            <div class="equity-box">
                <h3>Total Change (Since Start)</h3>
                <div class="equity-change ${totalChangeClass}">
                    <span>${totalArrow}</span>
                    <span>$${Math.abs(totalChange).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
                    <span>(${totalChangePct >= 0 ? '+' : ''}${totalChangePct.toFixed(2)}%)</span>
                </div>
                <div class="equity-meta">
                    From: $${(changeTotal.value_usdt || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})} | 
                    Duration: ${durationHours < 1 ? `${(durationHours * 60).toFixed(0)}m` : `${durationHours.toFixed(1)}h`}
                </div>
            </div>
        `;

        html += '</div>';

        document.getElementById(targetId).innerHTML = html;
    } catch (err) {
        document.getElementById('equityChangesPortfolio').innerHTML = `<p style="color:#dc2626;">Failed to load: ${err.message}</p>`;
    }
}

// Combined portfolio loader (equity + holdings)
async function loadPortfolio() {
    await Promise.all([
        loadEquityChanges(),
        loadHoldings()
    ]);
}

// Load Activity Timeline (NEW - replaces pipeline and role decisions)
async function loadActivity() {
    try {
        const symbol = document.getElementById('activitySymbol')?.value || '';
        const role = document.getElementById('activityRole')?.value || '';
        const limit = document.getElementById('activityLimit')?.value || '20';
        
        let url = `${API_BASE}/api/export/decisions?format=json&page=1&page_size=${limit}`;
        if (symbol) url += `&symbol=${symbol}`;
        if (role) url += `&role=${role}`;
        
        const res = await fetch(url);
        const data = await res.json();
        const container = document.getElementById('activityContainer');
        
        if (!data.data || data.data.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">📭</div>
                    <div class="empty-state-title">No Activity Found</div>
                    <div class="empty-state-text">No decisions recorded yet. Try running an agent manually.</div>
                </div>
            `;
            return;
        }
        
        container.innerHTML = data.data.map(d => renderActivityItem(d)).join('');
    } catch (err) {
        document.getElementById('activityContainer').innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">⚠️</div>
                <div class="empty-state-title">Failed to Load Activity</div>
                <div class="empty-state-text">${err.message}</div>
            </div>
        `;
    }
}

function renderActivityItem(decision) {
    const timestamp = decision.timestamp_ms ? formatBeijing(new Date(decision.timestamp_ms)) : formatBeijing(decision.timestamp);
    const input = decision.ai_input || {};
    const output = decision.ai_output || {};
    const role = input.role || 'legacy';
    const symbols = input.symbols || [];
    const symbol = symbols[0] || 'Unknown';
    
    let roleIcon = '';
    let roleBadgeClass = '';
    let roleLabel = '';
    
    if (role === 'strategist') {
        roleIcon = '🧠';
        roleBadgeClass = 'strategist';
        roleLabel = 'Strategist';
    } else if (role === 'tactician') {
        roleIcon = '📋';
        roleBadgeClass = 'tactician';
        roleLabel = 'Tactician';
    } else if (role === 'executor') {
        roleIcon = '⚡';
        roleBadgeClass = 'executor';
        roleLabel = 'Executor';
    } else if (role === 'executor_quick_exit') {
        roleIcon = '⚡💨';
        roleBadgeClass = 'executor_quick_exit';
        roleLabel = 'Quick Exit';
    } else {
        roleIcon = '🤖';
        roleBadgeClass = 'executor';
        roleLabel = 'Legacy';
    }
    
    let previewContent = '';
    let detailedContent = '';
    
    if (role === 'strategist') {
        previewContent = renderStrategistPreview(output, input);
        detailedContent = renderStrategistActivity(output, symbol, input);
    } else if (role === 'tactician') {
        previewContent = renderTacticianPreview(output);
        detailedContent = renderTacticianActivity(output, symbol);
    } else if (role === 'executor' || role === 'executor_quick_exit') {
        previewContent = renderExecutorPreview(output);
        detailedContent = renderExecutorActivity(output, role === 'executor_quick_exit');
    }
    
    const itemId = `activity-${decision.id}`;
    return `
        <div class="activity-item ${roleBadgeClass}" id="${itemId}" onclick="toggleActivityItem(this)">
            <div class="activity-header">
                <div class="activity-title">
                    <span style="font-size: 24px;">${roleIcon}</span>
                    <div>
                        <h3>${symbol}</h3>
                        <span class="role-badge ${roleBadgeClass}">${roleLabel}</span>
                    </div>
                </div>
                <div style="display: flex; align-items: flex-start; gap: 20px;">
                    <div class="activity-meta">
                        <span style="font-weight: 600;">${timestamp}</span>
                        <span>Provider: ${decision.provider || 'Unknown'}</span>
                    </div>
                    <span class="expand-icon">▼</span>
                </div>
            </div>
            <div class="activity-preview">
                ${previewContent}
            </div>
            <div class="activity-details">
                ${detailedContent}
            </div>
        </div>
    `;
}

function toggleActivityItem(element) {
    element.classList.toggle('expanded');
}

function renderStrategistPreview(output, input) {
    // For Strategist records, the trend data is in ai_output directly
    const overall = output?.overall_trend || 'unknown';
    const confidence = output?.confidence || 0;
    return `
        <div style="display: flex; gap: 16px; font-size: 13px;">
            <div><span style="color: #6b7280;">Trend:</span> <strong style="color: #1f2937;">${overall}</strong></div>
            <div><span style="color: #6b7280;">Confidence:</span> <strong style="color: #1f2937;">${(confidence * 100).toFixed(0)}%</strong></div>
        </div>
    `;
}

function renderTacticianPreview(output) {
    const status = output.status || 'unknown';
    const steps = output.plan?.steps || [];
    const confidence = output.confidence || 0;
    const planType = output.plan_type || (output.metadata && output.metadata.plan_type) || (output.plan && output.plan.plan_type) || 'n/a';
    return `
        <div style="display: flex; gap: 16px; font-size: 13px;">
            <div><span style="color: #6b7280;">Status:</span> <span class="status-chip status-${status}" style="font-size: 12px;">${status}</span></div>
            <div><span style="color: #6b7280;">Steps:</span> <strong style="color: #1f2937;">${steps.length}</strong></div>
            <div><span style="color: #6b7280;">Confidence:</span> <strong style="color: #1f2937;">${(confidence * 100).toFixed(0)}%</strong></div>
            <div><span style="color: #6b7280;">Plan Type:</span> <span class="status-chip" style="font-size: 12px;">${escapeHtml(planType)}</span></div>
        </div>
    `;
}

function renderExecutorPreview(output) {
    const orders = output.orders || [];
    const status = output.status || 'unknown';
    return `
        <div style="display: flex; gap: 16px; font-size: 13px;">
            <div><span style="color: #6b7280;">Orders:</span> <strong style="color: #1f2937;">${orders.length}</strong></div>
            <div><span style="color: #6b7280;">Status:</span> <span class="status-chip status-${status}" style="font-size: 12px;">${status}</span></div>
        </div>
    `;
}

function renderStrategistActivity(output, symbol, input) {
    // For Strategist records, all trend data is in ai_output directly (not in ai_input.strategist)
    const overall = output?.overall_trend || 'unknown';
    const nextDays = output?.next_days_trend || 'unknown';
    const next24h = output?.next_24h_trend || 'unknown';
    const nextHours = output?.next_few_hours_trend || 'unknown';
    const confidence = output?.confidence || 0;
    const rationale = output?.rationale || 'No rationale provided';
    
    const getTrendClass = (trend) => {
        const t = trend.toLowerCase();
        if (t.includes('bull') || t === 'up') return 'bullish';
        if (t.includes('bear') || t === 'down') return 'bearish';
        if (t === 'neutral') return 'neutral';
        if (t === 'mixed') return 'mixed';
        return '';
    };
    
    return `
        <div class="activity-section">
            <h4>📊 Trend Analysis</h4>
            <div class="trend-grid">
                <div class="trend-item">
                    <label>Overall</label>
                    <div class="value ${getTrendClass(overall)}">${overall}</div>
                </div>
                <div class="trend-item">
                    <label>Next Days</label>
                    <div class="value ${getTrendClass(nextDays)}">${nextDays}</div>
                </div>
                <div class="trend-item">
                    <label>Next 24H</label>
                    <div class="value ${getTrendClass(next24h)}">${next24h}</div>
                </div>
                <div class="trend-item">
                    <label>Next Hours</label>
                    <div class="value ${getTrendClass(nextHours)}">${nextHours}</div>
                </div>
            </div>
        </div>
        <div class="activity-section">
            <h4>🎯 Confidence</h4>
            <div style="margin-top: 8px;">
                <div class="confidence-bar">
                    <div class="confidence-fill" style="width: ${confidence * 100}%"></div>
                </div>
                <div style="text-align: center; font-size: 18px; font-weight: 700; color: #1f2937;">
                    ${(confidence * 100).toFixed(0)}%
                </div>
            </div>
        </div>
        <div class="activity-section full">
            <h4>💭 Analysis Rationale</h4>
            <div class="rationale-box">${rationale}</div>
        </div>
    `;
}

function renderTacticianActivity(output, symbol) {
    const status = output.status || 'unknown';
    const confidence = output.confidence || 0;
    const rationale = output.rationale || 'No rationale provided';
    const actions = output.actions || [];
    const steps = output.plan?.steps || [];
    const planType = output.plan_type || (output.metadata && output.metadata.plan_type) || (output.plan && output.plan.plan_type) || 'n/a';
    
    const statusChipHtml = `<span class="status-chip status-${status}">${status}</span>`;
    
    let planContent = '';
    if (status === 'hold') {
        planContent = `
            <div class="activity-section full">
                <h4>📋 Decision</h4>
                <div class="plan-summary">
                    <div class="plan-item">
                        <span class="label">Status</span>
                        ${statusChipHtml}
                    </div>
                </div>
            </div>
        `;
    } else if (steps.length > 0) {
        planContent = `
            <div class="activity-section full">
                <h4>📋 Trading Plan (${steps.length} steps)</h4>
                <div class="execution-steps">
                    ${steps.map((step, idx) => `
                        <div class="execution-step">
                            <div class="step-icon ${step.side || 'hold'}">${step.side === 'buy' ? '📈' : step.side === 'sell' ? '📉' : '⏸️'}</div>
                            <div class="step-content">
                                <div class="step-title">Step ${idx + 1}: ${(step.action || 'enter').toUpperCase()} ${(step.side || '').toUpperCase()}</div>
                                <div class="step-details">
                                    ${step.size_usd ? `$${step.size_usd.toFixed(2)}` : ''}
                                    ${step.entry_price ? `@ $${step.entry_price.toFixed(2)}` : ''}
                                    ${step.leverage > 1 ? `(${step.leverage}x leverage)` : ''}
                                </div>
                            </div>
                            <span class="status-chip status-${step.status || 'pending'}">${step.status || 'pending'}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }
    
    return `
        <div class="activity-section">
            <h4>📊 Plan Summary</h4>
            <div class="plan-summary">
                <div class="plan-item">
                    <span class="label">Status</span>
                    ${statusChipHtml}
                </div>
                <div class="plan-item">
                    <span class="label">Confidence</span>
                    <span class="value">${(confidence * 100).toFixed(0)}%</span>
                </div>
                <div class="plan-item">
                    <span class="label">Steps</span>
                    <span class="value">${steps.length}</span>
                </div>
                <div class="plan-item">
                    <span class="label">Plan Type</span>
                    <span class="value">${escapeHtml(planType)}</span>
                </div>
            </div>
        </div>
        <div class="activity-section">
            <h4>💭 Rationale</h4>
            <div class="rationale-box">${rationale}</div>
        </div>
        ${planContent}
    `;
}

function renderExecutorActivity(output, isQuickExit) {
    const orders = output.orders || [];
    const rationale = output.rationale || 'No rationale provided';
    const confidence = output.confidence || 0;
    
    const title = isQuickExit ? '⚡ Quick Exit Execution' : '⚡ Order Execution';
    
    return `
        <div class="activity-section">
            <h4>${title}</h4>
            <div class="execution-steps">
                ${orders.length === 0 ? '<p style="color: #9ca3af; font-size: 13px;">No orders executed</p>' : orders.map(order => `
                    <div class="execution-step">
                        <div class="step-icon ${order.action || 'hold'}">${order.action === 'buy' ? '📈' : order.action === 'sell' ? '📉' : '⏸️'}</div>
                        <div class="step-content">
                            <div class="step-title">${(order.action || 'hold').toUpperCase()} ${order.symbol || ''}</div>
                            <div class="step-details">
                                ${order.size_usd ? `$${order.size_usd.toFixed(2)}` : ''}
                                ${order.leverage > 1 ? `(${order.leverage}x leverage)` : ''}
                            </div>
                        </div>
                    </div>
                `).join('')}
            </div>
        </div>
        <div class="activity-section">
            <h4>💭 Execution Notes</h4>
            <div class="rationale-box">${rationale}</div>
            ${isQuickExit ? '<p style="margin-top: 8px; color: #d97706; font-size: 12px; font-weight: 600;">⚠️ Interrupted by newer plan</p>' : ''}
        </div>
    `;
}

function changeAIPage(delta) {
    if (!aiTotal && delta > 0 && !aiHasMore) return;
    const totalPages = aiTotal ? Math.max(1, Math.ceil(aiTotal / 1)) : Infinity;
    const next = aiTotal ? Math.min(Math.max(1, aiDataPage + delta), totalPages) : Math.max(1, aiDataPage + delta);
    if (next !== aiDataPage) {
        const prev = aiDataPage;
        loadAIData(next).then(() => {
            const out = document.getElementById('aiOutput').textContent || '';
            if (out.includes('No data available')) {
                aiDataPage = prev;
            }
        });
    }
}

// Initialize
checkStatus();
loadConfig();
loadPortfolio();
setInterval(checkStatus, 5000);
// Auto-refresh portfolio every 15s
setInterval(loadPortfolio, 15000);
