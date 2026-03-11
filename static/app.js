/**
 * Email Scraper Pro — SaaS UI (Redesign)
 */

// ─── State ──────────────────────────────────────
const getMeta = (metaName) => {
    const el = document.querySelector(`meta[name="${metaName}"]`);
    return el ? el.getAttribute('content') : '';
};

// Read from injected <meta> tags instead of hardcoding secrets in frontend code
const SUPABASE_URL = getMeta('supabase-url') || "";
const SUPABASE_ANON_KEY = getMeta('supabase-anon-key') || "";

// Prevent 'already declared' syntax crash on hot-reloading browser tabs.
if (!window.supabaseClient && SUPABASE_URL && SUPABASE_ANON_KEY) {
    window.supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
}
var supabase = window.supabaseClient;

let token = null;
let currentUser = null;
let currentJobId = null;
let ws = null;
let results = [];
let startTime = null;
let statsInterval = null;
let isSignupMode = false;
let autoScroll = true;

// ─── Init ───────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    // Listen for auth changes natively from Supabase
    supabase.auth.onAuthStateChange((event, session) => {
        if (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED') {
            token = session.access_token;
            checkAuth(); // Fetches our local backend data (lazy-provisioning free credits)
        } else if (event === 'SIGNED_OUT') {
            token = null;
            currentUser = null;
            if (window.location.pathname === '/dashboard') {
                window.location.href = '/';
            } else {
                document.getElementById('auth-screen').style.display = 'flex';
                document.getElementById('app-container').style.display = 'none';
            }
        }
    });
    
    // Check initial session
    supabase.auth.getSession().then(({ data: { session } }) => {
        if (session) {
            token = session.access_token;
            checkAuth();
        } else {
            // Fix flicker: If no session, either redirect to landing page (from dashboard), or show auth
            if (window.location.pathname === '/dashboard') {
                window.location.href = '/';
            } else {
                const authEl = document.getElementById('auth-screen');
                if (authEl) authEl.style.display = 'flex';
                document.getElementById('app-container').style.display = 'none';
            }
        }
    });
    
    // Auto-scroll pause on wheel
    const feed = document.getElementById('results-feed');
    if (feed) {
        feed.addEventListener('wheel', () => {
            autoScroll = false;
        });
    }
});

function toggleAdvanced() {
    const el = document.getElementById('advanced-filters');
    const btn = document.getElementById('btn-toggle-adv');
    if (el.classList.contains('collapsed')) {
        el.classList.remove('collapsed');
        btn.innerHTML = 'Hide Advanced Filters <i class="fas fa-chevron-up"></i>';
    } else {
        el.classList.add('collapsed');
        btn.innerHTML = 'Show Advanced Filters <i class="fas fa-chevron-down"></i>';
    }
}

// ─── Auth ───────────────────────────────────────
function toggleAuthMode(e) {
    if(e) e.preventDefault();
    isSignupMode = !isSignupMode;
    const btn = document.getElementById('auth-btn');
    const toggle = document.getElementById('auth-toggle');
    const toggleText = document.getElementById('auth-toggle-text');
    const nameField = document.getElementById('auth-name-field');
    const tosField = document.getElementById('auth-tos-field');

    if (isSignupMode) {
        btn.textContent = 'Sign Up Free';
        toggle.textContent = 'Log In';
        toggleText.textContent = 'Already have an account?';
        nameField.style.display = 'flex';
        tosField.style.display = 'block';
    } else {
        btn.textContent = 'Log In';
        toggle.textContent = 'Sign Up Free';
        toggleText.textContent = "Don't have an account?";
        nameField.style.display = 'none';
        tosField.style.display = 'none';
    }
    document.getElementById('auth-error').style.display = 'none';
}

async function handleAuth() {
    const email = document.getElementById('auth-email').value.trim();
    const password = document.getElementById('auth-password').value;
    const name = document.getElementById('auth-name').value.trim();
    const errorEl = document.getElementById('auth-error');

    if (!email || !password) {
        errorEl.textContent = 'Please enter email and password';
        errorEl.style.display = 'block';
        return;
    }

    const btn = document.getElementById('auth-btn');
    btn.disabled = true;
    btn.textContent = 'Authenticating...';

    try {
        let authResult;
        if (isSignupMode) {
            authResult = await supabase.auth.signUp({
                email: email,
                password: password,
                options: {
                    data: { full_name: name }
                }
            });
            if (authResult.data.user && authResult.data.user.identities && authResult.data.user.identities.length === 0) {
                 throw new Error("Email already registered");
            }
            if (!authResult.error && authResult.data.user) {
                errorEl.style.color = 'var(--success)';
                errorEl.textContent = 'Check your email for the confirmation link!';
                errorEl.style.display = 'block';
                return; // Stop here, require email verification
            }
        } else {
            authResult = await supabase.auth.signInWithPassword({
                email: email,
                password: password,
            });
        }

        if (authResult.error) throw authResult.error;

    } catch (e) {
        errorEl.style.color = 'var(--danger)';
        errorEl.textContent = e.message;
        errorEl.style.display = 'block';
    } finally {
        if(btn) {
             btn.disabled = false;
             btn.textContent = isSignupMode ? 'Sign Up Free' : 'Log In';
        }
    }
}

async function handleGoogleOAuth() {
    try {
        const { error } = await supabase.auth.signInWithOAuth({
            provider: 'google',
            options: {
                redirectTo: window.location.origin
            }
        });
        if (error) throw error;
    } catch (error) {
        console.error('Error logging in with Google:', error.message);
        alert(error.message);
    }
}

async function checkAuth() {
    try {
        // We hit the backend /api/auth/me using the Supabase JWT. 
        // This triggers the lazy-provisioning logic to grant the 500 credits locally 
        // if this is their first time logging in!
        const res = await fetch('/api/auth/me', {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!res.ok) throw new Error('Could not fetch user profile details');
        currentUser = await res.json();
        showApp();
    } catch (e) {
        console.warn("Backend auth check failed", e);
        supabase.auth.signOut();
    }
}

function showApp() {
    if (window.location.pathname !== '/dashboard') {
        // On landing/pricing pages: don't redirect — just swap CTAs to "Go to Dashboard"
        document.querySelectorAll('a[onclick*="openAuthModal"]').forEach(link => {
            link.style.display = 'none'; // Hide the "Log In" text link
        });
        document.querySelectorAll('button[onclick*="openAuthModal"]').forEach(btn => {
            if (btn.textContent.includes('Pro')) {
                btn.textContent = 'Upgrade in Dashboard →';
            } else {
                btn.textContent = 'Go to Dashboard →';
            }
            btn.onclick = () => { window.location.href = '/dashboard'; };
        });
        // Hide auth modal if it was open
        const authScreen = document.getElementById('auth-screen');
        if (authScreen) authScreen.style.display = 'none';
        return;
    }
    
    document.getElementById('auth-screen').style.display = 'none';
    document.getElementById('app-container').style.display = 'grid';
    updateCreditDisplay();
    document.getElementById('user-email-display').textContent = currentUser.email;

    // Fix refresh state loss: Check if a job was running before refresh
    const savedJobId = localStorage.getItem('activeJobId');
    if (savedJobId) {
        resumeJob(savedJobId);
    }
}

async function logout(e) {
    if(e) e.preventDefault();
    await supabase.auth.signOut();
}

function updateCreditDisplay() {
    if (!currentUser) return;
    const total = currentUser.totalCredits || (currentUser.freeCredits + currentUser.paidCredits);
    document.getElementById('credits-display').textContent = total.toLocaleString();

    let detail = '';
    if (currentUser.freeCredits > 0) detail += `${currentUser.freeCredits} free`;
    if (currentUser.paidCredits > 0) {
        if (detail) detail += ' + ';
        detail += `${currentUser.paidCredits.toLocaleString()} paid`;
    }
    if (total === 0) detail = 'No credits remaining';
    document.getElementById('credits-detail').textContent = detail;
    
    // Progress bar (fake 1000 max for free)
    const maxCreds = currentUser.paidCredits > 0 ? (total + 500) : 500;
    const pct = Math.min((total / maxCreds) * 100, 100);
    document.getElementById('credits-progress').style.width = pct + '%';
    if(pct < 20) document.getElementById('credits-progress').style.backgroundColor = 'var(--warning)';
    if(pct < 5) document.getElementById('credits-progress').style.backgroundColor = 'var(--danger)';
}

// ─── API Helper ─────────────────────────────────
async function api(url, options = {}) {
    const headers = { ...options.headers, 'Authorization': `Bearer ${token}` };
    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(options.body);
    }
    const res = await fetch(url, { ...options, headers });
    if (res.status === 401) {
        logout(new Event('click'));
        throw new Error('Session expired');
    }
    return res;
}

// ─── Tab Switch ─────────────────────────────────
function switchTab(tabId, el) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if (el) el.classList.add('active');
    document.querySelectorAll('.tab-view').forEach(v => v.classList.remove('active'));
    document.getElementById('tab-' + tabId).classList.add('active');

    if (tabId === 'results') renderStaticResults();
    if (tabId === 'history') loadHistory();
}

// ─── Core Scraper Actions ───────────────────────
async function launchScrape() {
    const keyword = document.getElementById('inp-keyword').value.trim();
    if (!keyword) {
        document.getElementById('inp-keyword').focus();
        return;
    }

    const totalCredits = currentUser.freeCredits + currentUser.paidCredits;
    if (totalCredits <= 0) {
        addTerminalLog('No credits remaining. Purchase more to continue.', 'error');
        return;
    }

    const body = {
        keyword,
        maxEmails: parseInt(document.getElementById('inp-max').value) || 500,
        country: document.getElementById('inp-country').value,
        language: document.getElementById('inp-lang').value,
        sortBy: document.getElementById('inp-sort').value,
        uploadDate: document.getElementById('inp-date').value,
        minSubscribers: parseInt(document.getElementById('inp-min-subs').value) || 0,
        maxSubscribers: parseInt(document.getElementById('inp-max-subs').value) || 0,
        timeoutMinutes: parseInt(document.getElementById('inp-timeout').value) || 30,
    };

    const btn = document.getElementById('launch-btn');
    btn.style.display = 'none';
    document.getElementById('stop-btn').style.display = 'block';

    try {
        const res = await api('/api/start', { method: 'POST', body });
        const data = await res.json();

        if (!res.ok) throw new Error(data.detail || 'Failed to start');

        currentJobId = data.jobId;
        localStorage.setItem('activeJobId', currentJobId); // Save state
        results = [];
        startTime = Date.now();
        autoScroll = true;

        // UI Reset
        document.getElementById('live-panel').style.display = 'block';
        const emptyState = document.getElementById('feed-empty');
        if (emptyState) emptyState.style.display = 'none';
        
        document.getElementById('results-header').style.display = 'grid';
        
        // Remove old result rows without deleting feed-empty
        document.querySelectorAll('#results-feed .result-row').forEach(row => row.remove());
        document.getElementById('log-console').innerHTML = '';
        document.getElementById('pulse-indicator').style.display = 'block';
        document.getElementById('progress-fill').style.width = '0%';
        document.getElementById('job-progress-text').textContent = `0 / ${body.maxEmails} Emails Collected`;
        


        updateStats();
        if (statsInterval) clearInterval(statsInterval);
        statsInterval = setInterval(() => updateStats(body.maxEmails), 1000);

        connectWebSocket(currentJobId, body.maxEmails);
        addTerminalLog(`Started targeted extraction for "${keyword}" (Task Size: ${data.maxEmails})`, 'info');
    } catch (e) {
        jobFinished(false);
        addTerminalLog(`Failed to start: ${e.message}`, 'error');
    }
}

async function stopJob() {
    if (!currentJobId) return;
    try {
        await api(`/api/stop/${currentJobId}`, { method: 'DELETE' });
        addTerminalLog('Stop signal sent globally to all workers...', 'warn');
        jobFinished(false);
    } catch (e) { }
}

function jobFinished(completed) {
    if (statsInterval) clearInterval(statsInterval);
    document.getElementById('launch-btn').style.display = 'block';
    document.getElementById('stop-btn').style.display = 'none';
    document.getElementById('queue-box').style.display = 'none';
    document.getElementById('pulse-indicator').style.display = 'none';
    localStorage.removeItem('activeJobId');
    refreshUser();
}

async function resumeJob(jobId) {
    try {
        const res = await api(`/api/status/${jobId}`);
        const statusData = await res.json();
        
        if (statusData.status === 'running' || statusData.status === 'queued') {
            currentJobId = jobId;
            results = [];
            
            // Restore UI panel
            document.getElementById('live-panel').style.display = 'block';
            const emptyState = document.getElementById('feed-empty');
            if (emptyState) emptyState.style.display = 'none';
            document.getElementById('results-header').style.display = 'grid';
            document.getElementById('launch-btn').style.display = 'none';
            document.getElementById('stop-btn').style.display = 'block';
            document.getElementById('pulse-indicator').style.display = 'block';
            
            // Re-fetch emails already found
            const resultsRes = await api(`/api/results/${jobId}?limit=500`);
            const resultsData = await resultsRes.json();
            
            if (resultsData.results && resultsData.results.length > 0) {
                resultsData.results.forEach((d, i) => {
                    results.push(d);
                    const count = i + 1;
                    addResultCard(d, count);
                });
                document.getElementById('stat-emails').textContent = results.length;
            }
            
            addTerminalLog(`Resumed session for active job. Reconnected to stream.`, 'info');
            
            // Re-connect WebSocket for the rest
            const maxEmailsToFetch = statusData.maxEmails || 500; // default if not provided
            connectWebSocket(currentJobId, maxEmailsToFetch);
        } else {
            localStorage.removeItem('activeJobId'); // Stale/finished
        }
    } catch (e) {
        console.error("Failed to resume job:", e);
        localStorage.removeItem('activeJobId');
    }
}

// ─── WebSocket & Live UI ────────────────────────
function connectWebSocket(jobId, maxEmails) {
    if (ws) {
        try { ws.close(); } catch (e) { }
    }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws/${jobId}?token=${encodeURIComponent(token)}`);

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        if (msg.type === 'email') {
            results.push(msg.data);
            const count = results.length;
            
            // Stats Panel
            document.getElementById('stat-emails').textContent = count;
            const pct = Math.min((count / maxEmails) * 100, 100);
            document.getElementById('progress-fill').style.width = pct + '%';
            document.getElementById('job-progress-text').textContent = `${count} / ${maxEmails} Emails Collected`;

            // Terminal
            const d = msg.data;
            const subs = d.subscribers ? `(${formatSubs(d.subscribers)} subs)` : '';
            addTerminalLog(`[${count}] Found ${d.email} -- ${d.channelName} ${subs}`, 'email');
            
            // Result Card
            addResultCard(d, count);
        }

        if (msg.type === 'done') {
            jobFinished(true);
            addTerminalLog(`Pipeline finished. ${msg.total} total emails extracted. Process status: ${msg.status}`, 'info');
            if (msg.stats) {
                document.getElementById('stat-channels').textContent = msg.stats.channels_scanned || 0;
            }
        }
    };

    ws.onclose = () => {
        if (document.getElementById('stop-btn').style.display === 'block') {
            // Reconnect if unprompted close
            setTimeout(() => { if (currentJobId === jobId) connectWebSocket(jobId, maxEmails); }, 2000);
        }
    };
}

// ─── Utility ────────────────────────────────────
const escapeHtml = (unsafe) => (unsafe || '').toString().replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");

// ─── Real Email Validation Logic ──────────────
function getEmailValidation(email) {
    if (!email) return { status: 'error', label: 'Invalid', icon: 'fa-times-circle', conf: 0 };
    
    const parts = email.split('@');
    if (parts.length !== 2 || (!parts[1].includes('.'))) {
        return { status: 'error', label: 'Invalid Syntax', icon: 'fa-exclamation-circle', conf: 0 };
    }
    const domain = parts[1].toLowerCase();
    
    // Check role-based
    const rolePrefixes = ['info', 'contact', 'support', 'hello', 'admin', 'sales', 'marketing', 'press', 'help', 'office', 'hi'];
    if (rolePrefixes.includes(parts[0].toLowerCase())) {
        return { status: 'risky', label: 'Role Address', icon: 'fa-users', conf: 60 };
    }
    
    // Check free providers
    const freeDomains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com', 'aol.com', 'proton.me', 'protonmail.com'];
    if (freeDomains.includes(domain)) {
        return { status: 'likely', label: 'Free Provider', icon: 'fa-envelope-open', conf: 85 };
    }
    
    return { status: 'verified', label: 'Valid Domain', icon: 'fa-check-circle', conf: 99 };
}

// ─── Card Rendering ─────────────────────────────

function buildSocialsHtml(d) {
    let html = '';
    if (d.instagram) html += `<a href="${escapeHtml(d.instagram)}" target="_blank" title="Instagram"><i class="fab fa-instagram"></i></a>`;
    if (d.twitter) html += `<a href="${escapeHtml(d.twitter)}" target="_blank" title="Twitter"><i class="fab fa-x-twitter"></i></a>`;
    if (d.tiktok) html += `<a href="${escapeHtml(d.tiktok)}" target="_blank" title="TikTok"><i class="fab fa-tiktok"></i></a>`;
    if (d.facebook) html += `<a href="${escapeHtml(d.facebook)}" target="_blank" title="Facebook"><i class="fab fa-facebook"></i></a>`;
    if (d.linkedin) html += `<a href="${escapeHtml(d.linkedin)}" target="_blank" title="LinkedIn"><i class="fab fa-linkedin"></i></a>`;
    if (d.website) html += `<a href="${escapeHtml(d.website)}" target="_blank" title="Website"><i class="fas fa-globe"></i></a>`;
    return html;
}

function addResultCard(d, index) {
    const feed = document.getElementById('results-feed');
    
    const safeEmail = escapeHtml(d.email);
    const safeChannelName = escapeHtml(d.channelName);
    const safeChannelUrl = escapeHtml(d.channelUrl);
    
    const v = getEmailValidation(d.email);
    const socialsHtml = buildSocialsHtml(d);
    
    const div = document.createElement('div');
    div.className = 'result-row filterable-card';
    div.dataset.search = (d.email + ' ' + d.channelName).toLowerCase();
    
    const confClass = v.conf >= 90 ? 'conf-green' : v.conf >= 70 ? 'conf-yellow' : 'conf-red';
    
    div.innerHTML = `
        <div class="rr-email" title="${safeEmail}">${safeEmail}</div>
        <a href="${safeChannelUrl}" target="_blank" class="rr-channel" title="${safeChannelName}">${safeChannelName}</a>
        <div class="rr-subs">${formatSubs(d.subscribers)}</div>
        <span class="rr-conf ${confClass}" title="${v.label}">${v.conf}%</span>
        <div class="rr-socials">${socialsHtml || '<span class="no-social">—</span>'}</div>
        <div class="rr-actions">
            <button class="rr-btn copy-btn" title="Copy Email"><i class="far fa-copy"></i></button>
            <a href="${safeChannelUrl}" target="_blank" class="rr-btn" title="Open Channel"><i class="fab fa-youtube"></i></a>
        </div>
    `;
    
    div.querySelector('.copy-btn').addEventListener('click', function() {
        copyEmailText(d.email, this);
    });
    
    feed.appendChild(div);
    if (autoScroll) {
        feed.scrollTop = feed.scrollHeight;
    }
}

function filterResults() {
    const term = document.getElementById('search-filter').value.toLowerCase();
    const cards = document.querySelectorAll('.filterable-card');
    cards.forEach(c => {
        if (c.dataset.search.includes(term)) c.style.display = 'block';
        else c.style.display = 'none';
    });
}

function copyEmailText(email, btn) {
    navigator.clipboard.writeText(email).then(() => {
        const orig = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-check"></i> Copied!';
        setTimeout(() => btn.innerHTML = orig, 1500);
    });
}

// ─── Terminal logging ───────────────────────────
function addTerminalLog(text, type = 'info') {
    const console_ = document.getElementById('log-console');
    if (!console_) return;
    const line = document.createElement('div');
    line.className = `log-${type}`;
    const t = new Date().toLocaleTimeString();
    line.textContent = `[${t}] ${text}`;
    console_.appendChild(line);
    console_.scrollTop = console_.scrollHeight;
}

// ─── Stats Update ───────────────────────────────
function updateStats(maxTarget) {
    if (!startTime) return;
    const elapsed = (Date.now() - startTime) / 1000;
    const mins = Math.floor(elapsed / 60);
    const secs = Math.floor(elapsed % 60);
    document.getElementById('stat-elapsed').textContent = `${mins}:${secs.toString().padStart(2, '0')}`;

    const rate = elapsed > 0 ? ((results.length / elapsed) * 60).toFixed(1) : '0';
    document.getElementById('stat-rate').textContent = rate;
}

// ─── Static Results Tab (Full History) ──────────
function renderStaticResults() {
    const feed = document.getElementById('results-feed-static');
    if (!feed) return; // If UI structure isn't ready
    
    const empty = document.getElementById('tab-results').querySelector('.feed-empty');
    if (results.length === 0) {
        if (empty) empty.style.display = 'flex';
        feed.style.display = 'none';
        document.getElementById('export-actions-full').style.display = 'none';
        return;
    }
    
    if (empty) empty.style.display = 'none';
    feed.style.display = 'flex';
    document.getElementById('export-actions-full').style.display = 'flex';
    document.getElementById('results-count').textContent = results.length;

    // Clear feed but keep header if it exists
    document.querySelectorAll('#results-feed-static .result-row').forEach(row => row.remove());
    
    // Check if we need to show/inject header
    let header = feed.querySelector('.results-header-row');
    if (header) {
        header.style.display = 'grid';
    }

    results.forEach((r, i) => {
        const safeEmail = escapeHtml(r.email);
        const safeChannelName = escapeHtml(r.channelName) || 'Unknown';
        const safeChannelUrl = escapeHtml(r.channelUrl) || '#';
        const v = getEmailValidation(r.email);
        const socialsHtml = buildSocialsHtml(r);
        
        const confClass = v.conf >= 90 ? 'conf-green' : v.conf >= 70 ? 'conf-yellow' : 'conf-red';
        
        const div = document.createElement('div');
        div.className = 'result-row';
        div.innerHTML = `
            <div class="rr-email" title="${safeEmail}">${safeEmail}</div>
            <a href="${safeChannelUrl}" target="_blank" class="rr-channel" title="${safeChannelName}">${safeChannelName}</a>
            <div class="rr-subs">${formatSubs(r.subscribers)}</div>
            <span class="rr-conf ${confClass}" title="${v.label}">${v.conf}%</span>
            <div class="rr-socials">${socialsHtml || '<span class="no-social">—</span>'}</div>
            <div class="rr-actions">
                <button class="rr-btn copy-btn" title="Copy Email"><i class="far fa-copy"></i></button>
                <a href="${safeChannelUrl}" target="_blank" class="rr-btn" title="Open Channel"><i class="fab fa-youtube"></i></a>
            </div>
        `;
        
        div.querySelector('.copy-btn').addEventListener('click', function() {
            copyEmailText(r.email, this);
        });
        
        feed.appendChild(div);
    });
}

// ─── History ────────────────────────────────────
async function loadHistory() {
    try {
        const res = await api('/api/jobs');
        const jobs = await res.json();
        const container = document.getElementById('history-list');

        if (jobs.length === 0) {
            container.innerHTML = '<div style="padding: 2rem; text-align: center; color: var(--text-muted);">No history found. Run a scrape to get started.</div>';
            return;
        }

        container.innerHTML = jobs.map(j => {
            const dateStr = new Date(j.createdAt).toLocaleString();
            let statusBadge = '';
            if (j.status==='completed') statusBadge = `<span style="color:var(--success);"><i class="fas fa-check-circle"></i> Complete</span>`;
            else if(j.status==='running') statusBadge = `<span style="color:var(--primary);"><i class="fas fa-spinner fa-spin"></i> Running</span>`;
            else statusBadge = `<span style="color:var(--text-dim);">${escapeHtml(j.status)}</span>`;

            return `
                <div class="result-card" style="cursor:pointer; display:flex; justify-content:space-between; align-items:center;" onclick="viewJob('${escapeHtml(j.id)}')">
                    <div>
                        <div style="font-weight:700; font-size:1.1rem; margin-bottom:4px;">${escapeHtml(j.keyword)}</div>
                        <div style="font-size:0.8rem; color:var(--text-muted);">${dateStr} &nbsp;·&nbsp; ${statusBadge}</div>
                    </div>
                    <div style="text-align:right;">
                        <span style="font-size:1.4rem; font-weight:800; color:var(--primary); display:block;">${j.total}</span>
                        <span style="font-size:0.7rem; color:var(--text-muted);">EMAILS</span>
                    </div>
                </div>
            `;
        }).join('');
    } catch (e) {
        console.error("Failed to load history:", e);
        document.getElementById('history-list').innerHTML = '<div style="color:var(--danger); padding:1rem;">Failed to load history</div>';
    }
}

async function viewJob(jobId) {
    try {
        const res = await api(`/api/results/${jobId}?limit=500`);
        const data = await res.json();
        
        if (data.results) {
            results = data.results;
        } else {
            results = [];
        }
        
        currentJobId = jobId;
        
        // Manually trigger tab switch to Results (Index 1 usually, but let's be safe)
        const tabs = document.querySelectorAll('.nav-item');
        let resultsTab = null;
        tabs.forEach(t => { if(t.textContent.includes('Results')) resultsTab = t; });
        
        switchTab('results', resultsTab);
    } catch (e) {
        console.error("View job failed:", e);
    }
}

async function refreshUser() {
    try {
        const res = await api('/api/auth/me');
        if (res.ok) {
            currentUser = await res.json();
            updateCreditDisplay();
        }
    } catch (e) { }
}

// ─── Exports ────────────────────────────────────
function exportCSV() {
    if (currentJobId) downloadFile(`/api/export/${currentJobId}?format=csv`);
}
function exportJSON() {
    if (currentJobId) downloadFile(`/api/export/${currentJobId}?format=json`);
}
function downloadFile(url) {
    const a = document.createElement('a');
    fetch(url, { headers: { 'Authorization': `Bearer ${token}` } })
        .then(r => r.blob())
        .then(blob => {
            const blobUrl = URL.createObjectURL(blob);
            a.href = blobUrl;
            a.download = '';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(blobUrl);
        });
}

// ─── Utils ──────────────────────────────────────
function formatSubs(n) {
    if (!n || n === 0) return '--';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
}
