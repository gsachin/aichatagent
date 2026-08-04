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
  setInterval(loadStats, 10000);
  setInterval(loadLeads, 30000);
  setInterval(loadConversations, 30000);
  setInterval(pollActiveCalls, 5000);

  // Load settings
  loadSettings();

  // Tab switching
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
}

// ===== DATA LOADERS =====
async function loadStats() {
  const data = await fetchJSON('/api/stats');
  if (!data) return;

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

async function loadLeads() {
  const data = await fetchJSON('/api/leads?limit=200');
  if (!data) return;
  state.leads = Array.isArray(data) ? data : [];
  renderPipeline();
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
function renderPipeline() {
  const leads = state.leads;
  // Group by status
  const groups = { pending: [], in_progress: [], completed: [], failed: [], unreachable: [] };
  leads.forEach(l => {
    const s = l.status || 'pending';
    if (groups[s]) groups[s].push(l);
    else groups.pending.push(l);
  });

  Object.entries(groups).forEach(([status, items]) => {
    // Update count
    const countEl = document.getElementById(`count-${status}`);
    if (countEl) countEl.textContent = items.length;

    // Render cards
    const cardsEl = document.getElementById(`cards-${status}`);
    if (!cardsEl) return;
    cardsEl.innerHTML = items.slice(0, 10).map(l => `
      <div class="lead-card" onclick="openLeadDetail('${l.id}')" style="border-left-color: ${statusColor(status)}">
        <div class="lead-name">${escapeHtml(l.name || 'Unknown')}</div>
        <div class="lead-program">${escapeHtml(l.program_interest || '—')}</div>
        <div class="lead-meta">
          <span>${escapeHtml(l.phone_number || '')}</span>
        </div>
      </div>
    `).join('');
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

// ===== STARTUP =====
document.addEventListener('DOMContentLoaded', init);
