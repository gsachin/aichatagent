// ===== APP STATE =====
const API_BASE = window.location.origin;
const state = {
  leads: [],
  conversations: [],
  stats: {},
  activeCalls: [],
  batchJobs: {},
  ui: { activeTab: 'tabConversations' }
};

// ===== UTILITIES =====
async function fetchJSON(path) {
  try {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    console.error(`API error: ${path}`, e);
    return null;
  }
}

// Track API errors per endpoint for retry
const _apiErrors = {};
function showError(sectionId, message, retryFn) {
  const el = document.getElementById(sectionId);
  if (!el) return;
  el.innerHTML = `<div class="error-banner"><span>⚠ ${message}</span><button onclick="(${retryFn.toString()})()">Retry</button></div>`;
}

// ===== SKELETON HELPERS =====
function showSkeleton(containerId, type) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (type === 'stats') {
    el.innerHTML = '<div class="skeleton-row">' + Array(5).fill('<div class="skeleton-card"></div>').join('') + '</div>';
  } else if (type === 'pipeline') {
    el.innerHTML = Array(5).fill('<div style="flex:1;min-width:180px"><div class="skeleton-line medium"></div><div class="skeleton-line" style="height:40px"></div><div class="skeleton-line short"></div><div class="skeleton-line short"></div></div>').join('');
  } else if (type === 'table') {
    el.innerHTML = Array(4).fill('<div class="skeleton-line medium"></div>').join('');
  }
}

function timeAgo(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  if (diff < 172800) return 'Yesterday';
  return d.toLocaleDateString();
}

function formatDuration(sec) {
  if (!sec || sec === 0) return '—';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m ${s.toString().padStart(2,'0')}s`;
}

function channelEmoji(ch) {
  const map = { inbound_call: '📞', outbound_call: '📤', whatsapp: '💬', streamlit: '🌐' };
  return map[ch] || '📝';
}

function statusColor(status) {
  const map = { pending: 'var(--yellow)', in_progress: 'var(--blue)', completed: 'var(--green)', failed: 'var(--red)', unreachable: 'var(--text-muted)' };
  return map[status] || 'var(--text-muted)';
}

// ===== CLOCK =====
function updateClock() {
  document.getElementById('clock').textContent = new Date().toLocaleString();
}

// ===== INIT =====
async function init() {
  updateClock();
  setInterval(updateClock, 30000);

  // Load initial data
  await Promise.all([loadStats(), loadLeads(), loadConversations()]);

  // Set up polling
  setInterval(loadSummary, 10000);
  setInterval(loadLeads, 30000);
  setInterval(loadConversations, 30000);

  // SSE for live calls
  connectSSE();

  // Load settings
  loadSettings();

  // Render calendar
  renderCalendar();

  // Tab switching
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
}

// ===== DATA LOADERS =====
async function loadSummary() {
  const data = await fetchJSON('/api/dashboard/summary');
  if (!data) return;
  const s = data.stats || data;
  state.stats = s;

  document.getElementById('statNewLeads').textContent = s.new_leads_today || 0;
  document.getElementById('statDueFollowUps').textContent = s.due_follow_ups || 0;
  document.getElementById('statHotLeads').textContent = s.hot_leads || 0;
  document.getElementById('statTotalPipeline').textContent = s.total_pipeline || 0;

  // Update active calls from SSE state
  const activeCount = Object.keys(state.activeCalls).length;
  document.getElementById('statActiveCalls').textContent = activeCount;
  const dot = document.getElementById('dotActiveCalls');
  if (activeCount > 0) { dot.classList.add('pulse'); }
  else { dot.classList.remove('pulse'); }

  // Update pipeline counts
  if (data.pipeline) {
    Object.entries(data.pipeline).forEach(([status, count]) => {
      const el = document.getElementById(`count-${status}`);
      if (el) el.textContent = count;
    });
  }

  // Recent activity from summary
  if (data.recent_activity) {
    state.conversations = data.recent_activity;
    renderActivity();
  }
}

async function loadStats() {
  // Legacy fallback
  const data = await fetchJSON('/api/stats');

  const health = await fetchJSON('/');
  state.stats = data;

  document.getElementById('statActiveCalls').textContent = '0'; // populated by pollActiveCalls
  document.getElementById('statNewLeads').textContent = data.new_leads_today || data.total_leads || 0;
  document.getElementById('statDueFollowUps').textContent = data.upcoming_follow_ups || 0;
  document.getElementById('statHotLeads').textContent = Math.floor((data.total_leads || 0) * 0.3);
  document.getElementById('statTotalPipeline').textContent = data.total_leads || 0;

  // System status
  if (health) {
    document.getElementById('systemStatus').textContent =
      health.database === 'connected' ? '🟢 System OK' : '🟠 DB Offline';
  }
}

async function loadLeads(searchTerm) {
  let url = '/api/leads?limit=200';
  if (searchTerm) url += `&search=${encodeURIComponent(searchTerm)}`;
  const data = await fetchJSON(url);
  if (data === null) {
    showError('pipelineBoard', 'Failed to load leads', () => loadLeads());
    return;
  }
  state.leads = Array.isArray(data) ? data : [];
  renderPipeline();
}

// Search handler
let _searchTimer = null;
function onSearchInput(val) {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => loadLeads(val), 300);
}

async function loadConversations() {
  const data = await fetchJSON('/api/conversations?limit=50');
  if (!data) return;
  state.conversations = Array.isArray(data) ? data : [];
  renderActivity();
  renderConversationsList();
}

async function pollActiveCalls() {
  const data = await fetchJSON('/api/call-queue?status=active');
  // activeCalls is any call in ringing or in-progress
  const active = [];
  document.getElementById('statActiveCalls').textContent = active.length;

  const dot = document.getElementById('dotActiveCalls');
  if (active.length > 0) {
    dot.classList.add('pulse');
  } else {
    dot.classList.remove('pulse');
  }
}

async function loadSettings() {
  const health = await fetchJSON('/');
  if (!health) return;
  document.getElementById('settingTunnel').textContent = health.endpoints?.twilio_webhook ?
    health.endpoints.twilio_webhook.replace('/twilio/voice','') : '—';
  document.getElementById('settingPhone').textContent = health.twilio_phone || '—';
  document.getElementById('settingDb').textContent = health.database || '—';
  document.getElementById('settingWorker').textContent = health.outbound_worker || '—';
}

// ===== RENDER: PIPELINE BOARD =====
const leadScores = {}; // Cache of lead_id -> score data
const tempMap = { hot: '🔥 Hot', warm: '🟡 Warm', cool: '🟠 Cool', cold: '🔴 Cold', dead: '⚫ Dead' };
const tempClass = { hot: 'badge-hot', warm: 'badge-warm', cool: 'badge-cool', cold: 'badge-cold', dead: '' };
const scoreColor = (s) => s >= 8 ? 'var(--green)' : s >= 5 ? 'var(--yellow)' : 'var(--red)';

async function loadLeadScores(leadIds) {
  // Fetch scores in batches (don't hammer the API)
  const toFetch = leadIds.filter(id => !leadScores[id]);
  if (toFetch.length === 0) return;
  for (const id of toFetch.slice(0, 20)) {
    try {
      const data = await fetchJSON(`/api/leads/${id}/score`);
      if (data) leadScores[id] = data;
    } catch(e) {}
  }
}

function renderPipeline() {
  const leads = state.leads;
  const groups = { pending: [], in_progress: [], completed: [], failed: [], unreachable: [] };
  leads.forEach(l => {
    const s = l.status || 'pending';
    if (groups[s]) groups[s].push(l);
    else groups.pending.push(l);
  });

  // Trigger score loading for visible leads
  const allIds = leads.map(l => l.id).filter(Boolean);
  loadLeadScores(allIds);

  Object.entries(groups).forEach(([status, items]) => {
    const countEl = document.getElementById(`count-${status}`);
    if (countEl) countEl.textContent = items.length;

    const cardsEl = document.getElementById(`cards-${status}`);
    if (!cardsEl) return;
    cardsEl.innerHTML = items.slice(0, 10).map(l => {
      const sc = leadScores[l.id] || {};
      const score = sc.score;
      const temp = sc.temperature;
      return `<div class="lead-card" onclick="openLeadDetail('${l.id}')" style="border-left-color: ${statusColor(status)}">
        <div class="lead-name">${escapeHtml(l.name || 'Unknown')}</div>
        <div class="lead-program">${escapeHtml(l.program_interest || '—')}</div>
        <div class="lead-meta">
          ${temp ? `<span class="badge ${tempClass[temp]||''}">${tempMap[temp]||temp}</span>` : ''}
          ${score ? `<span class="badge" style="color:${scoreColor(score)}">⭐${score}</span>` : ''}
        </div>
      </div>`;
    }).join('');
    if (items.length > 10) {
      cardsEl.innerHTML += `<div style="font-size:0.75rem;color:var(--text-muted);padding:0.5rem;text-align:center">+ ${items.length - 10} more</div>`;
    }
  });
}

// ===== RENDER: RECENT ACTIVITY =====
function renderActivity() {
  const convs = state.conversations.slice(0, 20);
  const tbody = document.getElementById('activityBody');
  if (!convs.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No recent activity</td></tr>';
    return;
  }
  tbody.innerHTML = convs.map(c => `
    <tr>
      <td>${timeAgo(c.created_at)}</td>
      <td>${channelEmoji(c.channel)} ${c.channel||'unknown'}</td>
      <td>${escapeHtml(c.phone_number||'N/A')}</td>
      <td>${escapeHtml(c.outcome||'—')}</td>
      <td>${formatDuration(c.call_duration_seconds)}</td>
      <td>
        <button class="btn btn-sm btn-secondary" onclick="openLeadDetail('${c.lead_id||''}')">📋</button>
      </td>
    </tr>
  `).join('');
}

// ===== RENDER: CONVERSATIONS LIST =====
function renderConversationsList() {
  filterConversations();
}

function filterConversations() {
  const search = (document.getElementById('convSearch')?.value || '').toLowerCase();
  const channel = document.getElementById('convChannel')?.value || 'All';

  let convs = state.conversations;
  if (channel !== 'All') convs = convs.filter(c => c.channel === channel);
  if (search) convs = convs.filter(c =>
    (c.transcript||'').toLowerCase().includes(search) ||
    (c.phone_number||'').toLowerCase().includes(search)
  );

  const el = document.getElementById('conversationsList');
  if (!el) return;
  if (!convs.length) {
    el.innerHTML = '<div class="conv-empty">No conversations found</div>';
    return;
  }
  el.innerHTML = convs.map(c => `
    <div class="conv-item">
      <div class="conv-header">
        <span class="conv-phone">${channelEmoji(c.channel)} ${escapeHtml(c.phone_number||'N/A')}</span>
        <span class="conv-meta">
          <span>${timeAgo(c.created_at)}</span>
          <span>${formatDuration(c.call_duration_seconds)}</span>
          <span>${escapeHtml(c.outcome||'')}</span>
        </span>
      </div>
      ${c.transcript ? `<div class="conv-transcript">${escapeHtml(c.transcript)}</div>` : '<div style="color:var(--text-muted);font-size:0.8rem">No transcript available</div>'}
    </div>
  `).join('');
}

// ===== LEAD DETAIL MODAL =====
async function openLeadDetail(leadId) {
  if (!leadId) return;
  const lead = state.leads.find(l => l.id === leadId);
  if (!lead) return;

  // Get conversations for this lead
  const convs = state.conversations.filter(c => c.lead_id === leadId);

  // Create modal
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  overlay.innerHTML = `
    <div class="modal">
      <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      <h2>${escapeHtml(lead.name || 'Unknown')}</h2>
      <div class="detail-grid">
        <div><div class="detail-label">Phone</div>${escapeHtml(lead.phone_number||'N/A')}</div>
        <div><div class="detail-label">Email</div>${escapeHtml(lead.email||'N/A')}</div>
        <div><div class="detail-label">Program</div>${escapeHtml(lead.program_interest||'N/A')}</div>
        <div><div class="detail-label">Status</div>${escapeHtml(lead.status||'pending')}</div>
        <div><div class="detail-label">Source</div>${escapeHtml(lead.source||'N/A')}</div>
        <div><div class="detail-label">Call Attempts</div>${lead.call_attempts||0}</div>
        <div><div class="detail-label">Created</div>${timeAgo(lead.created_at)}</div>
        <div><div class="detail-label">Last Called</div>${lead.last_called_at ? timeAgo(lead.last_called_at) : 'Never'}</div>
      </div>
      ${lead.notes ? `<div style="margin-bottom:1rem"><div class="detail-label">Notes</div>${escapeHtml(lead.notes)}</div>` : ''}
      <div style="display:flex;gap:0.5rem;margin-bottom:1.5rem">
        <button class="btn btn-primary btn-sm" onclick="callLead('${lead.id}')">📞 Call Now</button>
        <button class="btn btn-secondary btn-sm" onclick="this.closest('.modal-overlay').remove()">Close</button>
      </div>
      <div class="conv-section">
        <h3>💬 Conversations (${convs.length})</h3>
        ${convs.length === 0 ? '<div class="conv-empty">No conversations yet</div>' :
          convs.map(c => `
            <div class="conv-item">
              <div class="conv-header">
                <span>${channelEmoji(c.channel)} ${timeAgo(c.created_at)}</span>
                <span class="conv-meta">${formatDuration(c.call_duration_seconds)} · ${escapeHtml(c.outcome||'—')}</span>
              </div>
              ${c.transcript ? `<div class="conv-transcript">${escapeHtml(c.transcript)}</div>` : ''}
            </div>
          `).join('')
        }
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
}

// ===== QUICK CALL =====
async function callLead(leadId) {
  const res = await fetchJSON(`/api/leads/${leadId}/call`);
  if (res && !res.error) {
    alert('Call queued!');
  } else {
    alert('Failed to queue call');
  }
}

function parseNumbers(text) {
  const lines = text.split(/[\n,]+/).map(l => l.trim()).filter(Boolean);
  return lines.map(line => {
    // Try: +1234567890, John Smith, MBA
    const parts = line.split(/[,\t]+/).map(p => p.trim());
    const phone = parts[0] || '';
    const name = parts[1] || document.getElementById('batchName').value || '';
    const program = parts[2] || document.getElementById('batchProgram').value || '';
    return { phone_number: phone, name, program_interest: program };
  }).filter(l => l.phone_number);
}

async function submitBatchCall() {
  const numbers = parseNumbers(document.getElementById('batchNumbers').value);
  if (!numbers.length) { alert('Enter at least one phone number'); return; }

  const res = await fetch(`${API_BASE}/api/quick-call/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ leads: numbers, mode: 'all_at_once' })
  });
  if (res.ok) {
    const data = await res.json();
    document.getElementById('batchStatus').classList.remove('hidden');
    document.getElementById('batchProgress').innerHTML =
      `<div class="batch-progress-bar"><div class="batch-progress-fill" style="width:0%"></div></div>
       <p>${data.queued} calls queued</p>`;
    startBatchPolling(data.batch_id);
  } else {
    // Fallback: queue one by one via single endpoint
    let count = 0;
    for (const lead of numbers) {
      await fetch(`${API_BASE}/api/quick-call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(lead)
      });
      count++;
    }
    document.getElementById('batchStatus').classList.remove('hidden');
    document.getElementById('batchProgress').innerHTML = `<p>${count} calls queued via single endpoint</p>`;
  }
}

async function submitSingleCall() {
  const number = document.getElementById('batchNumbers').value.trim().split('\n')[0].trim();
  if (!number) { alert('Enter a phone number'); return; }
  const lead = parseNumbers(number)[0];
  const res = await fetch(`${API_BASE}/api/quick-call`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(lead)
  });
  if (res.ok) {
    alert('Call queued!');
  } else {
    alert('Failed to queue call');
  }
}

function startBatchPolling(batchId) {
  const interval = setInterval(async () => {
    const data = await fetchJSON(`/api/quick-call/batch/${batchId}`);
    if (!data) { clearInterval(interval); return; }
    const pct = data.total > 0 ? Math.round((data.completed / data.total) * 100) : 0;
    document.getElementById('batchProgress').innerHTML = `
      <div class="batch-progress-bar"><div class="batch-progress-fill" style="width:${pct}%"></div></div>
      <p>${data.completed}/${data.total} done · ${data.ringing||0} active · ${data.waiting||0} waiting</p>
    `;
    if (data.results) {
      document.getElementById('batchResults').innerHTML = data.results.map(r => `
        <div class="batch-result">${r.phone}: ${r.status} ${r.outcome ? '· '+r.outcome : ''} ${r.duration ? '· '+formatDuration(r.duration) : ''}</div>
      `).join('');
    }
    if (data.completed >= data.total) clearInterval(interval);
  }, 3000);
}

// ===== TAB SWITCHING =====
function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector(`[data-tab="${tabId}"]`).classList.add('active');
  document.getElementById(tabId).classList.add('active');
  state.ui.activeTab = tabId;
}

// ===== SCROLL TO SECTION =====
function scrollToSection(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
}

// ===== UTILS =====
function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ===== SSE: LIVE CALL MONITOR =====
function connectSSE() {
  const es = new EventSource(`${API_BASE}/api/calls/live?stream=true`);
  es.addEventListener('call_started', (e) => {
    const data = JSON.parse(e.data);
    state.activeCalls[data.call_sid] = data;
    renderLiveCalls();
    document.getElementById('statActiveCalls').textContent = Object.keys(state.activeCalls).length;
  });
  es.addEventListener('transcript', (e) => {
    const data = JSON.parse(e.data);
    if (state.activeCalls[data.call_sid]) {
      if (!state.activeCalls[data.call_sid].transcripts) {
        state.activeCalls[data.call_sid].transcripts = [];
      }
      state.activeCalls[data.call_sid].transcripts.push(data.dialogue || '');
      renderLiveCalls();
    }
  });
  es.addEventListener('call_ended', (e) => {
    const data = JSON.parse(e.data);
    delete state.activeCalls[data.call_sid];
    renderLiveCalls();
    document.getElementById('statActiveCalls').textContent = Object.keys(state.activeCalls).length;
  });
  es.onerror = () => { /* Reconnect handled by browser */ };
  state._sse = es;
}

function renderLiveCalls() {
  const container = document.getElementById('liveCallsContent');
  const callIds = Object.keys(state.activeCalls);
  if (!callIds.length) {
    container.innerHTML = '<div class="empty-state"><span class="empty-icon">📞</span><p>No active calls right now</p><p class="empty-hint">Calls will appear here in real-time when active</p></div>';
    return;
  }
  container.innerHTML = callIds.map(sid => {
    const call = state.activeCalls[sid];
    const transcripts = call.transcripts || [];
    return `<div class="call-monitor-card">
      <div class="call-header">
        <span class="call-direction">${call.direction === 'outbound' ? '📤 Outbound' : '📞 Inbound'}</span>
        <span class="call-duration">🟢 Active</span>
      </div>
      <div class="call-transcript">
        ${transcripts.map(t => {
          const lines = t.split('\n');
          return lines.map(line => {
            if (line.startsWith('Assistant:')) return `<div class="ai-line">${escapeHtml(line)}</div>`;
            if (line.startsWith('Caller:')) return `<div class="caller-line">${escapeHtml(line)}</div>`;
            return `<div>${escapeHtml(line)}</div>`;
          }).join('');
        }).join('')}
      </div>
    </div>`;
  }).join('');
}

// ===== CALENDAR TAB =====
function renderCalendar() {
  const el = document.getElementById('calendarView');
  if (!el) return;

  const now = new Date();
  const daysToShow = 7;
  let html = '<div class="calendar-grid">';

  for (let i = 0; i < daysToShow; i++) {
    const d = new Date(now);
    d.setDate(d.getDate() + i);
    const dateStr = d.toISOString().slice(0, 10);
    const dayName = d.toLocaleDateString('en-US', { weekday: 'short' });
    const monthDay = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    const isToday = i === 0;

    // Find follow-ups for this day
    const dayFollowUps = (state.leads || []).filter(l =>
      l.next_follow_up && l.next_follow_up.startsWith(dateStr)
    );

    html += `<div class="calendar-day ${isToday ? 'today' : ''}">
      <div class="cal-day-header">${dayName}</div>
      <div class="cal-date">${monthDay}</div>
      <div class="cal-count">${dayFollowUps.length} follow-up${dayFollowUps.length !== 1 ? 's' : ''}</div>
      ${dayFollowUps.map(l => `
        <div class="cal-item" onclick="openLeadDetail('${l.id}')">
          ${l.next_follow_up?.includes('T') ? l.next_follow_up.slice(11,16) : ''} ${escapeHtml(l.name || l.phone_number)}
        </div>
      `).join('')}
    </div>`;
  }

  html += '</div>';
  el.innerHTML = html;
}

// Periodically refresh calendar
setInterval(() => { renderCalendar(); }, 60000);

// ===== REPORTS TAB =====
function renderReports() {
  const el = document.getElementById('reportsContent');
  if (!el) return;
  const leads = state.leads || [];
  const convs = state.conversations || [];

  const today = new Date().toISOString().slice(0, 10);
  const todayConvs = convs.filter(c => c.created_at && c.created_at.startsWith(today));
  const completedLeads = leads.filter(l => l.status === 'completed');
  const totalCalls = convs.filter(c => c.channel === 'inbound_call' || c.channel === 'outbound_call');
  const interested = convs.filter(c => c.outcome === 'interested');

  el.innerHTML = `
    <div class="stat-row" style="padding:0">
      <div class="stat-card">
        <div class="stat-number">${todayConvs.length}</div>
        <div class="stat-label">📞 Calls Today</div>
      </div>
      <div class="stat-card">
        <div class="stat-number">${totalCalls.length}</div>
        <div class="stat-label">📊 Total Calls</div>
      </div>
      <div class="stat-card">
        <div class="stat-number">${interested.length}</div>
        <div class="stat-label">🎯 Interested</div>
      </div>
      <div class="stat-card">
        <div class="stat-number">${completedLeads.length}</div>
        <div class="stat-label">✅ Converted</div>
      </div>
      <div class="stat-card">
        <div class="stat-number">${leads.length}</div>
        <div class="stat-label">👥 Total Leads</div>
      </div>
    </div>
    <div style="margin-top:1rem"><strong>Channel Breakdown</strong></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;margin-top:0.5rem">
      ${['inbound_call','outbound_call','whatsapp','streamlit'].map(ch => {
        const count = convs.filter(c => c.channel === ch).length;
        const emoji = {inbound_call:'📞', outbound_call:'📤', whatsapp:'💬', streamlit:'🌐'}[ch]||'📝';
        const label = {inbound_call:'Inbound Calls', outbound_call:'Outbound Calls', whatsapp:'WhatsApp', streamlit:'Web Chat'}[ch]||ch;
        return `<div style="background:var(--bg-body);padding:0.6rem;border-radius:var(--radius);display:flex;justify-content:space-between">
          <span>${emoji} ${label}</span><strong>${count}</strong>
        </div>`;
      }).join('')}
    </div>
  `;
}

// Update reports when data changes
const _origRenderActivity = renderActivity;
renderActivity = function() {
  _origRenderActivity();
  renderReports();
};

// ===== STARTUP =====
document.addEventListener('DOMContentLoaded', init);
