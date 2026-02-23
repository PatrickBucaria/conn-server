// Conn Dashboard — Read-Only Monitoring

const state = {
    token: localStorage.getItem('conn_token') || '',
    conversations: [],
    activeIds: new Set(),
    selectedId: null,
    history: [],
    ws: null,
    wsConnected: false,
    streamingText: {},      // conversation_id -> accumulated text
    activeTools: {},        // conversation_id -> {tool, summary}
    uptime: 0,
    pollTimer: null,
};

// ---- Auth ----

function authenticate() {
    const input = document.getElementById('token-input');
    const token = input.value.trim();
    if (!token) return;

    state.token = token;
    localStorage.setItem('conn_token', token);

    // Test the token with a health + conversations call
    fetchJSON('/conversations').then(() => {
        showDashboard();
    }).catch(() => {
        document.getElementById('auth-error').textContent = 'Invalid token or server unreachable';
        state.token = '';
        localStorage.removeItem('conn_token');
    });
}

function logout() {
    state.token = '';
    localStorage.removeItem('conn_token');
    if (state.ws) state.ws.close();
    clearInterval(state.pollTimer);
    document.getElementById('dashboard').classList.add('hidden');
    document.getElementById('auth-screen').classList.remove('hidden');
    document.getElementById('token-input').value = '';
    document.getElementById('auth-error').textContent = '';
}

function showDashboard() {
    document.getElementById('auth-screen').classList.add('hidden');
    document.getElementById('dashboard').classList.remove('hidden');
    startPolling();
    connectWebSocket();
}

// ---- API ----

async function fetchJSON(path) {
    const resp = await fetch(path, {
        headers: { 'Authorization': `Bearer ${state.token}` },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
}

async function refreshAll() {
    try {
        const [convData, activeData, healthData, previewData] = await Promise.all([
            fetchJSON('/conversations'),
            fetchJSON('/conversations/active'),
            fetch('/health').then(r => r.json()),
            fetchJSON('/preview/status'),
        ]);

        state.conversations = convData.conversations || [];
        state.activeIds = new Set(activeData.active_conversation_ids || []);
        state.uptime = healthData.uptime_seconds || 0;

        updateHeader(previewData.previews || []);
        renderConversationList();

        // If we have a selected conversation, refresh its history
        if (state.selectedId) {
            await loadHistory(state.selectedId);
        }
    } catch (e) {
        console.error('Refresh failed:', e);
    }
}

async function loadHistory(conversationId) {
    try {
        const data = await fetchJSON(`/conversations/${conversationId}/history`);
        state.history = data.history || [];
        renderMessages();
    } catch (e) {
        console.error('Failed to load history:', e);
    }
}

function startPolling() {
    refreshAll();
    state.pollTimer = setInterval(refreshAll, 5000);
}

// ---- WebSocket ----

function connectWebSocket() {
    if (state.ws && state.ws.readyState <= 1) return;

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${location.host}/ws/chat`;

    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        // Authenticate
        state.ws.send(JSON.stringify({ type: 'auth', token: state.token }));
    };

    state.ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleWsMessage(msg);
    };

    state.ws.onclose = () => {
        state.wsConnected = false;
        updateWsBadge();
        // Reconnect after 3s
        setTimeout(connectWebSocket, 3000);
    };

    state.ws.onerror = () => {
        state.wsConnected = false;
        updateWsBadge();
    };
}

function handleWsMessage(msg) {
    switch (msg.type) {
        case 'auth_ok':
            state.wsConnected = true;
            updateWsBadge();
            break;

        case 'text_delta':
            handleTextDelta(msg);
            break;

        case 'tool_start':
            state.activeTools[msg.conversation_id] = {
                tool: msg.tool,
                summary: msg.input_summary,
            };
            if (msg.conversation_id === state.selectedId) {
                renderStreamingArea();
            }
            updateConvItemStatus(msg.conversation_id, true);
            break;

        case 'tool_done':
            delete state.activeTools[msg.conversation_id];
            if (msg.conversation_id === state.selectedId) {
                renderStreamingArea();
            }
            break;

        case 'message_complete':
            // Clear streaming state for this conversation
            delete state.streamingText[msg.conversation_id];
            delete state.activeTools[msg.conversation_id];
            state.activeIds.delete(msg.conversation_id);

            if (msg.conversation_id === state.selectedId) {
                loadHistory(msg.conversation_id);
                hideStreamingArea();
            }
            updateConvItemStatus(msg.conversation_id, false);
            // Refresh conversation list for updated timestamps
            refreshConversationList();
            break;

        case 'conversation_created':
            refreshConversationList();
            break;

        case 'conversation_renamed':
            renameConversation(msg.conversation_id, msg.name);
            break;

        case 'cancelled':
            delete state.streamingText[msg.conversation_id];
            delete state.activeTools[msg.conversation_id];
            state.activeIds.delete(msg.conversation_id);
            if (msg.conversation_id === state.selectedId) {
                hideStreamingArea();
                loadHistory(msg.conversation_id);
            }
            updateConvItemStatus(msg.conversation_id, false);
            break;

        case 'ping':
            // Respond to server keepalive pings
            if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify({ type: 'pong' }));
            }
            break;

        case 'error':
            console.error('Server error:', msg.detail);
            break;
    }
}

function handleTextDelta(msg) {
    const cid = msg.conversation_id;
    if (!state.streamingText[cid]) {
        state.streamingText[cid] = '';
    }
    state.streamingText[cid] += msg.text;
    state.activeIds.add(cid);
    updateConvItemStatus(cid, true);

    if (cid === state.selectedId) {
        renderStreamingArea();
    }
}

// ---- Rendering ----

function updateHeader(previews) {
    const healthBadge = document.getElementById('health-badge');
    const agentsBadge = document.getElementById('agents-badge');

    healthBadge.textContent = `Up ${formatUptime(state.uptime)}`;
    healthBadge.className = 'badge badge-ok';

    const activeCount = state.activeIds.size;
    agentsBadge.textContent = `${activeCount} agent${activeCount !== 1 ? 's' : ''}`;
    agentsBadge.className = activeCount > 0 ? 'badge badge-active' : 'badge';
}

function updateWsBadge() {
    const badge = document.getElementById('ws-badge');
    if (state.wsConnected) {
        badge.textContent = 'WS: connected';
        badge.className = 'badge badge-connected';
    } else {
        badge.textContent = 'WS: disconnected';
        badge.className = 'badge badge-disconnected';
    }
}

function renderConversationList() {
    const list = document.getElementById('conversation-list');
    const countEl = document.getElementById('conv-count');
    countEl.textContent = state.conversations.length;

    list.innerHTML = state.conversations.map(conv => {
        const isActive = state.activeIds.has(conv.id);
        const isSelected = conv.id === state.selectedId;
        const project = conv.working_dir ? conv.working_dir.split('/').pop() : '';
        const time = formatTime(conv.last_message_at);

        return `
            <div class="conv-item ${isSelected ? 'selected' : ''}"
                 onclick="selectConversation('${conv.id}')"
                 data-id="${conv.id}">
                <div class="conv-indicator ${isActive ? 'active' : ''}"></div>
                <div class="conv-info">
                    <div class="conv-name">${escapeHtml(conv.name)}</div>
                    <div class="conv-meta">
                        ${project ? `<span class="conv-project">${escapeHtml(project)}</span>` : ''}
                        <span>${time}</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function selectConversation(id) {
    state.selectedId = id;

    // Update sidebar selection
    document.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('selected', el.dataset.id === id);
    });

    // Show chat container
    document.getElementById('chat-placeholder').classList.add('hidden');
    document.getElementById('chat-container').classList.remove('hidden');

    // Update chat header
    const conv = state.conversations.find(c => c.id === id);
    if (conv) {
        document.getElementById('chat-title').textContent = conv.name;

        const statusEl = document.getElementById('chat-status');
        const isActive = state.activeIds.has(id);
        statusEl.textContent = isActive ? 'Active' : '';
        statusEl.className = isActive ? 'chat-status active' : 'chat-status';

        const project = conv.working_dir ? conv.working_dir.split('/').pop() : '';
        document.getElementById('chat-project').textContent = project ? `Project: ${project}` : '';
        document.getElementById('chat-branch').textContent = conv.git_branch ? `Branch: ${conv.git_branch}` : '';
        document.getElementById('chat-tools').textContent = conv.allowed_tools
            ? `Tools: ${conv.allowed_tools.join(', ')}`
            : '';
    }

    // Load history
    loadHistory(id);

    // Show streaming if active
    if (state.streamingText[id] || state.activeTools[id]) {
        renderStreamingArea();
    } else {
        hideStreamingArea();
    }
}

function renderMessages() {
    const container = document.getElementById('chat-messages');

    if (state.history.length === 0) {
        container.innerHTML = '<div class="no-messages">No messages yet</div>';
        return;
    }

    container.innerHTML = state.history.map(msg => {
        const role = msg.role;
        const html = renderMarkdown(msg.text);
        return `
            <div class="message ${role}">
                <div class="message-role">${role}</div>
                <div class="message-bubble">${html}</div>
            </div>
        `;
    }).join('');

    // Scroll to bottom
    container.scrollTop = container.scrollHeight;
}

function renderStreamingArea() {
    const area = document.getElementById('streaming-area');
    const textEl = document.getElementById('streaming-text');
    const labelEl = area.querySelector('.streaming-label');

    area.classList.remove('hidden');

    const cid = state.selectedId;
    const tool = state.activeTools[cid];
    const text = state.streamingText[cid] || '';

    if (tool) {
        labelEl.textContent = `Using ${tool.tool}: ${tool.summary}`;
    } else {
        labelEl.textContent = 'Streaming...';
    }

    // Show last ~500 chars of streaming text
    const display = text.length > 500 ? '...' + text.slice(-500) : text;
    textEl.textContent = display;

    // Auto-scroll streaming area
    area.scrollTop = area.scrollHeight;
}

function hideStreamingArea() {
    document.getElementById('streaming-area').classList.add('hidden');
    document.getElementById('streaming-text').textContent = '';
}

function updateConvItemStatus(conversationId, isActive) {
    const el = document.querySelector(`.conv-item[data-id="${conversationId}"] .conv-indicator`);
    if (el) {
        el.classList.toggle('active', isActive);
    }
}

async function refreshConversationList() {
    try {
        const [convData, activeData] = await Promise.all([
            fetchJSON('/conversations'),
            fetchJSON('/conversations/active'),
        ]);
        state.conversations = convData.conversations || [];
        state.activeIds = new Set(activeData.active_conversation_ids || []);
        renderConversationList();
    } catch (e) {
        console.error('Failed to refresh conversation list:', e);
    }
}

function renameConversation(id, name) {
    const conv = state.conversations.find(c => c.id === id);
    if (conv) {
        conv.name = name;
        renderConversationList();
        if (id === state.selectedId) {
            document.getElementById('chat-title').textContent = name;
        }
    }
}

// ---- Markdown ----

function renderMarkdown(text) {
    if (!text) return '';

    let html = escapeHtml(text);

    // Code blocks (``` ... ```)
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        return `<pre><code>${code.trim()}</code></pre>`;
    });

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Italic
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Blockquotes
    html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');

    // Unordered lists
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

    // Line breaks (but not inside pre/code blocks)
    html = html.replace(/\n/g, '<br>');

    // Clean up extra <br> around block elements
    html = html.replace(/<br><(pre|h[1-3]|ul|blockquote)/g, '<$1');
    html = html.replace(/<\/(pre|h[1-3]|ul|blockquote)><br>/g, '</$1>');

    return html;
}

// ---- Utilities ----

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatUptime(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
}

function formatTime(isoString) {
    if (!isoString) return '';
    try {
        const d = new Date(isoString);
        const now = new Date();
        const diffMs = now - d;
        const diffMin = Math.floor(diffMs / 60000);
        const diffHr = Math.floor(diffMs / 3600000);
        const diffDay = Math.floor(diffMs / 86400000);

        if (diffMin < 1) return 'just now';
        if (diffMin < 60) return `${diffMin}m ago`;
        if (diffHr < 24) return `${diffHr}h ago`;
        if (diffDay < 7) return `${diffDay}d ago`;

        return d.toLocaleDateString();
    } catch {
        return '';
    }
}

// ---- Init ----

document.getElementById('token-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') authenticate();
});

// Auto-connect if token is stored
if (state.token) {
    fetchJSON('/conversations').then(() => {
        showDashboard();
    }).catch(() => {
        // Token invalid, show auth screen
        state.token = '';
        localStorage.removeItem('conn_token');
    });
}
