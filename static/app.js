const UI_KEY = 'webterm-ui-state-v2';
const PROFILES_KEY = 'webterm-profiles-v2';

const HOMELAB_SERVERS = [
  { name: 'macbook-pro-de-damien', ip: '100.110.54.5', aliases: ['macbook', 'macbook-pro'] },
  { name: 'pixel-8-pro', ip: '100.72.17.1', aliases: ['pixel', 'mpixel'] },
  { name: 'truenas', ip: '192.168.1.11', aliases: ['nas'] },
  { name: 'image-store', ip: '100.118.221.26', aliases: ['image-store', '192.168.1.129'] },
  { name: 'hermes', ip: '100.69.53.14', aliases: ['192.168.1.200'] },
  { name: 'tailscale', ip: '100.89.14.119', aliases: ['router'] },
  { name: 'gx10', ip: '100.118.187.64', aliases: ['gx10-dab6', 'spark', '192.168.1.130'] },
  { name: 'netochka', ip: '100.119.43.10', aliases: ['netochka-01', 'netoska', '192.168.1.127'] },
  { name: 'dh7-syno-01', ip: '100.107.84.55', aliases: ['synology', '192.168.1.126'] },
];

const state = {
  sessions: [],
  activeSession: null,
  activeTabBySession: {},
  tmuxSessions: [],
  templates: [],
  panelEditor: null,
  discoveredServices: [],
  serviceSnapshotByHost: {},
  controlSessionsAvailable: false,
  sessionsVersion: null,
  pendingDeletedSessions: new Set(),
  dirtySessions: new Set(),
  // Local edits not yet accepted by the control server. Cleared only by a
  // successful PUT; while set, the poll retries the save instead of adopting.
  unsavedChanges: false,
  keysMode: false,
};

const addModal = document.getElementById('add-modal');
const addWorkspaceType = document.getElementById('add-workspace-type');
const addServerSelect = document.getElementById('add-server-select');
const addNewServerWrap = document.getElementById('add-new-server-wrap');
const addKnownServerWrap = document.getElementById('add-known-server-wrap');
const addKnownServer = document.getElementById('add-known-server');
const addServerIp = document.getElementById('add-server-ip');
const addProjectPath = document.getElementById('add-project-path');
const addTabOptions = document.getElementById('add-tab-options');
const addLabel = document.getElementById('add-label');
const addExistingWrap = document.getElementById('add-existing-wrap');
const addExistingSession = document.getElementById('add-existing-session');
const addNewSessionWrap = document.getElementById('add-new-session-wrap');
const addNewSession = document.getElementById('add-new-session');
const panelModal = document.getElementById('panel-modal');
const panelTarget = document.getElementById('panel-target');
const panelList = document.getElementById('panel-list');
const panelServices = document.getElementById('panel-services');
const panelServicesNote = document.getElementById('panel-services-note');
let saveSessionsTimer = null;
let saveInFlight = false;
let viewportUpdateRaf = 0;

function updateViewportMetrics() {
  if (viewportUpdateRaf) cancelAnimationFrame(viewportUpdateRaf);
  viewportUpdateRaf = requestAnimationFrame(() => {
    viewportUpdateRaf = 0;
    const vv = window.visualViewport;
    const height = Math.max(1, Math.round(vv?.height || window.innerHeight || document.documentElement.clientHeight));
    const offsetTop = Math.max(0, Math.round(vv?.offsetTop || 0));
    const root = document.documentElement;
    root.style.setProperty('--rr-viewport-height', `${height}px`);
    root.style.setProperty('--rr-viewport-offset-top', `${offsetTop}px`);
  });
}

function installViewportResizeHandling() {
  updateViewportMetrics();
  window.addEventListener('resize', updateViewportMetrics, { passive: true });
  window.addEventListener('orientationchange', updateViewportMetrics, { passive: true });
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', updateViewportMetrics, { passive: true });
    window.visualViewport.addEventListener('scroll', updateViewportMetrics, { passive: true });
  }
}

function loadUIState() {
  try {
    const raw = localStorage.getItem(UI_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      if (typeof parsed.activeSession === 'string') state.activeSession = parsed.activeSession;
      if (parsed.activeTabBySession && typeof parsed.activeTabBySession === 'object') {
        state.activeTabBySession = parsed.activeTabBySession;
      }
    }
  } catch (_) {}
}

function saveUIState() {
  try {
    localStorage.setItem(UI_KEY, JSON.stringify({
      activeSession: state.activeSession,
      activeTabBySession: state.activeTabBySession,
    }));
  } catch (_) {}
}

function saveProfiles() {
  state.unsavedChanges = true;
  try {
    localStorage.setItem(PROFILES_KEY, JSON.stringify(state.sessions));
  } catch (_) {}
  queueSessionSaveToControl();
}

// Adopt the control server's sessions verbatim as the in-memory view. The
// server is the source of truth; localStorage is only a last-known-good cache.
function adoptServerSessions(sessions, version) {
  state.sessions = sessions;
  state.sessionsVersion = version ?? null;
  try {
    localStorage.setItem(PROFILES_KEY, JSON.stringify(state.sessions));
  } catch (_) {}
}

function mergeSessionForControlSave(localSession, remoteSession) {
  if (!localSession) return remoteSession;
  if (!remoteSession) return localSession;
  const merged = { ...remoteSession, ...localSession };
  if (!merged.project && remoteSession.project) {
    merged.project = remoteSession.project;
  }
  return merged;
}

// Reconcile a rejected write against the server's current state (409 path
// only). Rules: local order always wins (reordering is a local action);
// content comes from whoever actually changed it — local for sessions the user
// edited (dirty) or created (not yet on the server), server otherwise (so a
// concurrent agent tab-add on an untouched session survives); sessions created
// elsewhere are appended.
function rebaseOntoServer(serverSessions) {
  const remote = Array.isArray(serverSessions) ? serverSessions : [];
  const remoteByName = new Map(remote.map((s) => [s.name, s]));
  const merged = [];
  const seen = new Set();
  state.sessions.forEach((localSession) => {
    if (!localSession || !localSession.name) return;
    if (state.pendingDeletedSessions.has(localSession.name)) return;
    const remoteSession = remoteByName.get(localSession.name);
    if (!remoteSession) {
      merged.push(localSession); // created locally, not yet saved
    } else if (state.dirtySessions.has(localSession.name)) {
      merged.push(mergeSessionForControlSave(localSession, remoteSession)); // user edited it: local content wins
    } else {
      merged.push(remoteSession); // untouched locally: server content is truth
    }
    seen.add(localSession.name);
  });
  remote.forEach((remoteSession) => {
    if (!remoteSession || !remoteSession.name || seen.has(remoteSession.name)) return;
    if (state.pendingDeletedSessions.has(remoteSession.name)) return;
    merged.push(remoteSession);
  });
  state.sessions = merged;
}

async function loadSessionsFromControl() {
  try {
    const response = await fetch('/sessions');
    if (!response.ok) {
      state.controlSessionsAvailable = false;
      return null;
    }
    const payload = await response.json();
    if (!Array.isArray(payload.sessions)) {
      state.controlSessionsAvailable = false;
      return null;
    }
    state.controlSessionsAvailable = true;
    return {
      sessions: payload.sessions.map(normalizeProfile).filter(Boolean),
      version: payload.version ?? null,
    };
  } catch (_) {
    // Network error (hub restarting, laptop asleep): transient. Keep polling;
    // only an HTTP answer without a sessions API means "not a control hub".
    return null;
  }
}

async function loadControlContext() {
  try {
    const response = await fetch('/control/context');
    if (!response.ok) return null;
    state.controlSessionsAvailable = true;
    const payload = await response.json();
    // Served through gateway.py (public access): panels must go through /m/<host>/<port>.
    state.viaGateway = payload.via_gateway === true;
    state.hubHost = String(payload.hub_host || '');
    return {
      machines: Array.isArray(payload.machines) ? payload.machines.map(normalizeProfile).filter(Boolean) : [],
      sessions: Array.isArray(payload.sessions) ? payload.sessions.map(normalizeProfile).filter(Boolean) : [],
      templates: Array.isArray(payload.session_templates)
        ? payload.session_templates.map((item) => ({
            id: String(item.id || ''),
            label: String(item.label || item.id || 'Template'),
            panels: normalizePanels(item.panels || []),
          })).filter((item) => item.id && item.panels.length)
        : [],
    };
  } catch (_) {
    return null;
  }
}

// Write the browser's intended session list through to the server verbatim
// (order included). The server is authoritative; a stale write is rejected
// with 409, after which we rebase onto the server's current state and retry.
async function flushSessionsToControl(retries = 2) {
  if (!state.controlSessionsAvailable || saveInFlight) return;
  saveInFlight = true;
  try {
    await putSessionsToControl(retries);
  } finally {
    saveInFlight = false;
  }
}

async function putSessionsToControl(retries) {
  try {
    const response = await fetch('/sessions', {
      method: 'PUT',
      keepalive: true, // finish the save even if the page is reloading
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ sessions: state.sessions, base_version: state.sessionsVersion }),
    });

    if (response.status === 409 && retries > 0) {
      const body = await response.json().catch(() => null);
      if (body && Array.isArray(body.sessions)) {
        rebaseOntoServer(body.sessions.map(normalizeProfile).filter(Boolean));
        state.sessionsVersion = body.version ?? null;
        renderSidebar();
        return putSessionsToControl(retries - 1);
      }
      return;
    }

    if (response.ok) {
      const body = await response.json().catch(() => null);
      state.sessionsVersion = body && body.version != null ? body.version : state.sessionsVersion;
      state.pendingDeletedSessions.clear();
      state.dirtySessions.clear();
      state.unsavedChanges = false;
      try {
        localStorage.setItem(PROFILES_KEY, JSON.stringify(state.sessions));
      } catch (_) {}
    }
  } catch (_) {}
}

function queueSessionSaveToControl() {
  if (!state.controlSessionsAvailable) return;
  if (saveSessionsTimer) clearTimeout(saveSessionsTimer);
  saveSessionsTimer = setTimeout(() => {
    saveSessionsTimer = null;
    flushSessionsToControl();
  }, 150);
}

async function refreshSessionsFromControlIfChanged() {
  if (!state.controlSessionsAvailable) return;
  // Skip the adopt while a local write is pending, so the poll can't stomp an
  // edit that hasn't been flushed yet.
  if (saveSessionsTimer || saveInFlight) return;
  // A save failed (hub down, conflicts exhausted): retry it rather than adopt
  // the server copy, which would silently drop the user's edits.
  if (state.unsavedChanges) {
    await flushSessionsToControl();
    return;
  }
  const result = await loadSessionsFromControl();
  if (!result) return;
  if (result.version != null && result.version === state.sessionsVersion) return;
  adoptServerSessions(result.sessions, result.version);
  await reloadSessions(state.activeSession);
}

function closeUpdateModal() {
  document.getElementById('update-modal').classList.remove('open');
}

// ── Terminal keypad (send F-keys / control keys to the active tmux session) ──
const KEYPAD_KEYS = [
  [['win-prev', '◀ Prev'], ['win-next', 'Next ▶'], ['win-new', '＋ New']],
  [['scroll', 'Scroll'], ['detach', 'Detach']],
  [['Escape', 'Esc'], ['Tab', 'Tab'], ['Enter', 'Enter'], ['C-c', '^C']],
  [['Left', '←'], ['Up', '↑'], ['Down', '↓'], ['Right', '→']],
];

function activeServer() {
  return state.sessions.find((s) => s.name === state.activeSession) || null;
}

function tmuxSessionFor(server) {
  if (!server) return '';
  const term = (server.tabs || []).find(
    (t) => t.service === 'terminal' || String(t.label || '').toLowerCase() === 'terminal'
  );
  if (term && term.path) {
    const m = /[?&]arg=([^&]+)/.exec(term.path);
    if (m) {
      try { return decodeURIComponent(m[1]); } catch (_) { return m[1]; }
    }
  }
  return server.name || '';
}

function buildKeysGrid() {
  const grid = document.getElementById('keys-grid');
  grid.innerHTML = '';
  for (const row of KEYPAD_KEYS) {
    const rowEl = document.createElement('div');
    rowEl.className = 'keys-row';
    for (const spec of row) {
      const key = Array.isArray(spec) ? spec[0] : spec;
      const label = Array.isArray(spec) ? spec[1] : spec;
      const btn = document.createElement('button');
      btn.className = 'key-btn';
      btn.type = 'button';
      btn.textContent = label;
      btn.onclick = () => sendKey(key, btn);
      rowEl.appendChild(btn);
    }
    grid.appendChild(rowEl);
  }
}

// The keyboard button switches the left panel between the workspace list and
// the keypad, rather than opening a popup.
function setKeysMode(on) {
  state.keysMode = on;
  document.getElementById('session-list').classList.toggle('hidden', on);
  document.getElementById('keys-panel').classList.toggle('hidden', !on);
  const sw = document.getElementById('send-keys-btn');
  sw.classList.toggle('on', on);
  sw.setAttribute('aria-checked', on ? 'true' : 'false');
  if (on) {
    const server = activeServer();
    document.getElementById('keys-flash').textContent = '';
    document.getElementById('keys-target').textContent = server
      ? `→ ${displayName(server)} · ${tmuxSessionFor(server) || 'no terminal'}`
      : 'No active workspace';
    buildKeysGrid();
  }
}

function toggleKeysMode() {
  setKeysMode(!state.keysMode);
}

function toggleFullscreen() {
  const el = document.documentElement;
  const req = el.requestFullscreen || el.webkitRequestFullscreen;
  const exit = document.exitFullscreen || document.webkitExitFullscreen;
  const isFull = document.fullscreenElement || document.webkitFullscreenElement;
  if (!isFull && req) req.call(el);
  else if (isFull && exit) exit.call(document);
}

function setRrMenu(show) {
  document.getElementById('rr-menu').classList.toggle('hidden', !show);
}

function setSidebarCollapsed(on) {
  document.body.classList.toggle('sidebar-collapsed', on);
  try { localStorage.setItem('rr_sidebar_collapsed', on ? '1' : ''); } catch (_) {}
}

async function sendKey(key, btn) {
  const flash = document.getElementById('keys-flash');
  const server = activeServer();
  if (!server) { flash.textContent = 'No active workspace'; return; }
  const session = tmuxSessionFor(server);
  if (!session) { flash.textContent = 'No terminal for this workspace'; return; }
  const host = sessionMachineHost(server) || '127.0.0.1';
  if (btn) btn.classList.add('flash');
  try {
    const res = await fetch('/tmux/send-keys', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ host, port: 7000, session, key }),
    }).then((r) => r.json());
    flash.textContent = res.status === 'sent'
      ? `sent ${key}`
      : `${key}: ${res.status || 'error'}${res.reason ? ' — ' + res.reason : ''}`;
  } catch (err) {
    flash.textContent = `${key}: ${err.message}`;
  } finally {
    if (btn) setTimeout(() => btn.classList.remove('flash'), 180);
  }
}

async function updateAllRemotes() {
  const branch = prompt('Update all remotes from which branch?', 'main');
  if (branch === null) return;
  const cleanedBranch = branch.trim() || 'main';

  const updateModal = document.getElementById('update-modal');
  const branchNote = document.getElementById('update-branch-note');
  const machineList = document.getElementById('update-machine-list');

  machineList.innerHTML = '';
  branchNote.textContent = `Triggering update from branch: ${cleanedBranch}...`;
  updateModal.classList.add('open');

  let payload;
  try {
    payload = await fetch('/admin/update-all-remotes', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ branch: cleanedBranch }),
    }).then((r) => r.json());
  } catch (err) {
    branchNote.textContent = `Error triggering updates: ${err.message}`;
    return;
  }

  const results = Array.isArray(payload.results) ? payload.results : [];
  if (!results.length) {
    branchNote.textContent = 'No remotes configured.';
    return;
  }

  branchNote.textContent = `Branch: ${cleanedBranch} — update triggered on ${results.length} machine${results.length === 1 ? '' : 's'}`;

  const machineStates = results.map((row) => {
    const name = row.machine || row.host || 'unknown';
    const host = row.host || '';
    const triggered = row.status === 'scheduled';

    const rowEl = document.createElement('div');
    rowEl.className = 'update-machine-row';

    const nameEl = document.createElement('div');
    nameEl.className = 'update-machine-name';
    nameEl.textContent = name;

    const commitsEl = document.createElement('div');
    commitsEl.className = 'update-machine-commits';
    commitsEl.textContent = triggered ? 'Waiting for update to apply...' : `Failed: ${row.reason || 'unknown'}`;

    const badge = document.createElement('div');
    badge.className = `update-status-badge ${triggered ? 'checking' : ''}`;
    badge.textContent = triggered ? 'checking' : 'error';

    rowEl.appendChild(nameEl);
    rowEl.appendChild(commitsEl);
    rowEl.appendChild(badge);
    machineList.appendChild(rowEl);

    return { name, host, triggered, commitsEl, badge, done: !triggered };
  });

  const MAX_POLLS = 20;
  let pollCount = 0;

  const poll = async () => {
    pollCount += 1;
    const pending = machineStates.filter((m) => !m.done);
    if (!pending.length || pollCount > MAX_POLLS) return;

    await Promise.all(pending.map(async (machine) => {
      try {
        const diag = await fetch('/admin/update-diagnostics/proxy', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ host: machine.host, hub_port: 7000, branch: cleanedBranch }),
        }).then((r) => r.json());

        const head = (diag.head || '').trim();
        const gitOut = (diag.git?.stdout || '').trim();
        const headHash = head.split(' ')[0];
        const remoteHash = gitOut.split('\t')[0];

        machine.commitsEl.innerHTML = '';

        const headLine = document.createElement('div');
        headLine.className = 'update-commit-line';
        const headLabel = document.createElement('span');
        headLabel.textContent = 'local:  ';
        headLine.appendChild(headLabel);
        headLine.appendChild(document.createTextNode(head || 'unknown'));

        const remoteLine = document.createElement('div');
        remoteLine.className = 'update-commit-line';
        const remoteLabel = document.createElement('span');
        remoteLabel.textContent = 'remote: ';
        remoteLine.appendChild(remoteLabel);
        remoteLine.appendChild(document.createTextNode(remoteHash ? remoteHash.slice(0, 12) : 'unknown'));

        machine.commitsEl.appendChild(headLine);
        machine.commitsEl.appendChild(remoteLine);

        if (headHash && remoteHash && remoteHash.startsWith(headHash)) {
          machine.badge.className = 'update-status-badge synced';
          machine.badge.textContent = 'synced';
          machine.done = true;
        } else if (diag.error) {
          machine.badge.className = 'update-status-badge';
          machine.badge.textContent = 'unreachable';
        } else {
          machine.badge.className = 'update-status-badge behind';
          machine.badge.textContent = 'behind';
        }
      } catch (_) {
        // keep polling
      }
    }));

    const stillPending = machineStates.filter((m) => !m.done);
    if (stillPending.length && pollCount <= MAX_POLLS) {
      setTimeout(poll, 3000);
    }
  };

  setTimeout(poll, 3000);
}

function serviceNameForLabel(label) {
  const key = String(label || '').trim().toLowerCase();
  if (key === 'terminal') return 'terminal';
  if (key === 'files') return 'files';
  if (key === 'hub') return 'hub';
  return '';
}

function normalizeTab(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const label = String(raw.label || '').trim();
  const port = Number(raw.port || 0);
  const service = raw.service ? String(raw.service).trim() : serviceNameForLabel(label);
  if (!label) return null;
  return {
    id: raw.id ? String(raw.id) : `tab-${Math.random().toString(36).slice(2, 10)}`,
    label,
    service: service || undefined,
    port: port > 0 ? port : undefined,
    path: raw.path ? String(raw.path) : undefined,
    protocol: raw.protocol ? String(raw.protocol) : undefined,
    ephemeral: raw.ephemeral === true ? true : undefined,
    new_tab: raw.new_tab === true ? true : undefined,
  };
}

function normalizeProfile(raw) {
  if (!raw || typeof raw !== 'object' || !raw.name) return null;
  const rawTabs = Array.isArray(raw.tabs) ? raw.tabs : Array.isArray(raw.panels) ? raw.panels : [];
  const tabs = rawTabs.map(normalizeTab).filter(Boolean);
  const host = raw.machine?.host ? String(raw.machine.host) : (raw.ip ? String(raw.ip) : '127.0.0.1');
  const machineName = raw.machine?.name ? String(raw.machine.name) : (raw.machineName ? String(raw.machineName) : String(raw.name));
  return {
    name: String(raw.name),
    display: raw.display ? String(raw.display) : undefined,
    color: raw.color ? String(raw.color) : undefined,
    priority: ['P0', 'P1', 'P2'].includes(raw.priority) ? raw.priority : undefined,
    machine: {
      name: machineName,
      host,
    },
    ip: host,
    project: raw.project ? String(raw.project) : undefined,
    tabs,
    panels: tabs,
  };
}

async function loadProfilesFromBootstrap() {
  const controlContext = await loadControlContext();
  if (controlContext && controlContext.templates.length) {
    state.templates = controlContext.templates;
  }
  const normalizedBootstrap = controlContext
    ? controlContext.machines
    : (await fetch('/machines').then((r) => r.json()).catch(() => []))
        .map(normalizeProfile)
        .filter(Boolean);
  // Server is the source of truth: pull sessions + version authoritatively.
  const control = await loadSessionsFromControl();
  if (control && control.sessions.length) {
    adoptServerSessions(control.sessions, control.version);
    return;
  }

  // Server unreachable OR empty: fall back to the localStorage cache, else the
  // bootstrap machine list. Seed the server if it was reachable-but-empty.
  const serverReachableEmpty = Boolean(control && control.sessions.length === 0);
  try {
    const raw = localStorage.getItem(PROFILES_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    const localProfiles = Array.isArray(parsed) ? parsed.map(normalizeProfile).filter(Boolean) : [];
    state.sessions = localProfiles.length ? localProfiles : normalizedBootstrap;
  } catch (_) {
    state.sessions = normalizedBootstrap;
  }
  state.sessionsVersion = null;
  if (serverReachableEmpty && state.sessions.length) {
    queueSessionSaveToControl();
  }
}

function slug(value) {
  return String(value).replace(/[^a-zA-Z0-9_-]/g, '-');
}

function makeUniqueName(base) {
  const cleaned = String(base || 'workspace').toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'workspace';
  const names = new Set(state.sessions.map((s) => s.name));
  if (!names.has(cleaned)) return cleaned;
  let i = 2;
  while (names.has(`${cleaned}-${i}`)) i += 1;
  return `${cleaned}-${i}`;
}

function displayName(server) {
  return server.display || server.name;
}

function frameIdFor(server, tab, endpoint) {
  const port = endpoint?.port || tab.port || 'na';
  return `frame-${slug(server.name)}-${slug(tab.id || tab.label)}-${port}`;
}

function resolveHost(host) {
  if (!host || host === '127.0.0.1' || host === 'localhost' || host === '::1') return window.location.hostname;
  return host;
}

function isLocalHost(host) {
  return !host || host === '127.0.0.1' || host === 'localhost' || host === '::1';
}

function homelabIpForName(text) {
  const key = String(text || '').trim().toLowerCase();
  if (!key) return '';
  const keyTokens = key.split(/[^a-z0-9._-]+/).filter(Boolean);
  for (const server of HOMELAB_SERVERS) {
    const names = [server.name, ...(server.aliases || [])].map((x) => x.toLowerCase());
    if (server.ip === key) return server.ip;
    if (names.includes(key)) return server.ip;
    if (names.some((n) => key.includes(n))) return server.ip;
    if (keyTokens.some((tok) => names.includes(tok))) return server.ip;
  }
  return '';
}

function sessionMachineHost(server) {
  const host = server.machine?.host || server.ip || '';
  const mapped = homelabIpForName(server.machine?.name) || homelabIpForName(displayName(server)) || homelabIpForName(server.name);
  if (isLocalHost(host) && mapped) return mapped;
  return host || mapped || '';
}

function hslToHex(h, s, l) {
  s /= 100; l /= 100;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => {
    const k = (n + h / 30) % 12;
    const c = l - a * Math.max(Math.min(k - 3, 9 - k, 1), -1);
    return Math.round(255 * c).toString(16).padStart(2, '0');
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

function colorForSession(server) {
  if (server.color) return server.color;
  const key = sessionMachineHost(server) || server.ip || '';
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = ((hash << 5) - hash) + key.charCodeAt(i);
  const hue = Math.abs(hash) % 360;
  return hslToHex(hue, 65, 55);
}


function updateSidebarColor() {
  const sidebar = document.getElementById('sidebar');
  const root = document.documentElement;
  const active = state.sessions.find((s) => s.name === state.activeSession);
  if (active) {
    const color = colorForSession(active);
    sidebar.style.setProperty('--active-color', color);
    root.style.setProperty('--active-color', color);
  } else {
    sidebar.style.removeProperty('--active-color');
    root.style.removeProperty('--active-color');
  }
}

function terminalSessionForSession(server) {
  const terminal = (server.tabs || []).find((p) => p.label === 'Terminal' || p.service === 'terminal');
  if (!terminal || !terminal.path) return '1';
  const idx = terminal.path.indexOf('?');
  if (idx < 0) return '1';
  const params = new URLSearchParams(terminal.path.slice(idx + 1));
  return params.get('arg') || '1';
}

function sanitizeSessionName(raw) {
  const cleaned = String(raw || '').trim().replace(/[^A-Za-z0-9._:-]+/g, '-');
  return cleaned || '1';
}

function clonePanels(source) {
  return (source.tabs || source.panels || []).map((p) => ({ ...p }));
}

function normalizePanels(panels) {
  if (!Array.isArray(panels)) return [];
  return panels.map(normalizeTab).filter(Boolean);
}

function defaultTemplates() {
  return [
    {
      id: 'standard',
      label: 'Standard',
      panels: [
        { label: 'Terminal', port: 7681 },
      ],
    },
  ];
}

async function loadSessionTemplates() {
  try {
    const payload = await fetch('/session-templates').then((r) => r.json());
    if (!Array.isArray(payload) || !payload.length) {
      state.templates = defaultTemplates();
      return;
    }
    state.templates = payload.map((item) => ({
      id: String(item.id || ''),
      label: String(item.label || item.id || 'Template'),
      panels: normalizePanels(item.panels || []),
    })).filter((item) => item.id && item.panels.length);
    if (!state.templates.length) state.templates = defaultTemplates();
  } catch (_) {
    state.templates = defaultTemplates();
  }
}

async function fetchMachinePanels(host) {
  const query = new URLSearchParams({ host: host || '127.0.0.1', port: '7000' }).toString();
  try {
    const payload = await fetch(`/machines/proxy?${query}`).then((r) => r.json());
    if (!payload.ok || !Array.isArray(payload.servers) || !payload.servers.length) {
      return null;
    }
    const first = payload.servers[0];
    return normalizePanels(first.panels || []);
  } catch (_) {
    return null;
  }
}

async function fetchRemoteServices(host) {
  const query = new URLSearchParams({ host: host || '127.0.0.1', port: '7000' }).toString();
  try {
    const payload = await fetch(`/services/proxy?${query}`).then((r) => r.json());
    const services = Array.isArray(payload.services)
      ? payload.services.map((service) => ({
          name: String(service.name || ''),
          label: String(service.label || service.name || 'Service'),
          port: Number(service.port || 0),
          path: service.path ? String(service.path) : '/',
          protocol: service.protocol ? String(service.protocol) : 'http',
          up: Boolean(service.up),
          enabled: service.enabled !== false,
          launchable: Boolean(service.launchable),
          embeddable: service.embeddable !== false,
          latency: Number(service.latency_ms || 0),
          error: service.error ? String(service.error) : '',
          sessionName: service.session_name ? String(service.session_name) : '',
          project: service.project ? String(service.project) : '',
        })).filter((service) => service.name && service.port > 0)
      : [];
    return {
      host: String(payload.host || host || 'unknown'),
      error: payload.error ? String(payload.error) : '',
      services,
    };
  } catch (err) {
    return {
      host: host || 'unknown',
      error: String(err.message || err),
      services: [],
    };
  }
}

function findService(services, names, labels) {
  const nameSet = new Set(names.map((name) => name.toLowerCase()));
  const labelSet = new Set(labels.map((label) => label.toLowerCase()));
  return services.find((service) => nameSet.has(service.name.toLowerCase()))
    || services.find((service) => labelSet.has(service.label.toLowerCase()))
    || null;
}

function isLegacyWorkspaceToolService(service) {
  const name = String(service?.name || '').toLowerCase();
  const label = String(service?.label || '').toLowerCase();
  if (label === 'notes' || label === 'skills') return true;
  if (name.startsWith('notes-') || name.startsWith('skills-')) return true;
  if (name.startsWith('files-')) return true;
  return false;
}

const DEFAULT_WORKSPACE_TABS = [
  { key: 'terminal', label: 'Terminal', service: 'terminal', port: 7681, path: '/', checked: true, required: true },
  { key: 'notes', label: 'Notes', service: 'builtin', port: 7000, builtin: 'notes', checked: true },
  { key: 'skills', label: 'Skills', service: 'builtin', port: 7000, builtin: 'skills', checked: true },
  { key: 'files', label: 'Files', service: 'builtin', port: 7000, builtin: 'files', checked: true },
];

function tabChoiceFromService(service, fallback = {}) {
  return {
    key: fallback.key || service.name || service.label,
    label: fallback.label || service.label || service.name || 'Service',
    service: service.name || fallback.service || '',
    port: Number(service.port || fallback.port || 0),
    path: service.path || fallback.path || '/',
    protocol: service.protocol || fallback.protocol || 'http',
    checked: Boolean(fallback.checked),
    required: Boolean(fallback.required),
  };
}

function builtinPath(workspaceName, kind) {
  return `/workspaces/${encodeURIComponent(workspaceName)}/${kind}`;
}

function renderWorkspaceTabOptions(workspaceName, services = []) {
  addTabOptions.innerHTML = '';
  const used = new Set();
  const choices = DEFAULT_WORKSPACE_TABS.map((fallback) => {
    const service = fallback.builtin ? null : findService(services, [fallback.service], [fallback.label]);
    used.add(String(fallback.service).toLowerCase());
    used.add(String(fallback.label).toLowerCase());
    const choice = service ? tabChoiceFromService(service, fallback) : { ...fallback, protocol: 'http' };
    if (fallback.builtin) choice.path = builtinPath(workspaceName, fallback.builtin);
    return choice;
  });

  services
    .filter((service) => service.embeddable !== false)
    .filter((service) => !isLegacyWorkspaceToolService(service))
    .forEach((service) => {
      const name = String(service.name || '').toLowerCase();
      const label = String(service.label || '').toLowerCase();
      if (used.has(name) || used.has(label)) return;
      used.add(name);
      used.add(label);
      choices.push(tabChoiceFromService(service));
    });

  choices.forEach((choice) => {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = Boolean(choice.checked);
    input.disabled = Boolean(choice.required);
    input.dataset.label = choice.label;
    input.dataset.service = choice.service;
    input.dataset.port = String(choice.port || 0);
    input.dataset.path = choice.path || '/';
    input.dataset.protocol = choice.protocol || 'http';
    if (choice.builtin) input.dataset.builtin = choice.builtin;
    label.appendChild(input);
    label.appendChild(document.createTextNode(choice.label));
    addTabOptions.appendChild(label);
  });
}

async function refreshWorkspaceTabOptions(host) {
  const workspaceName = proposedWorkspaceName();
  renderWorkspaceTabOptions(workspaceName);
  const snapshot = await fetchRemoteServices(host);
  renderWorkspaceTabOptions(workspaceName, snapshot.services || []);
}

function buildWorkspaceTabs() {
  const workspaceName = proposedWorkspaceName();
  const tabs = Array.from(addTabOptions.querySelectorAll('input[type="checkbox"]'))
    .filter((input) => input.checked || input.disabled)
    .map((input) => normalizeTab({
      label: input.dataset.label,
      service: input.dataset.service,
      port: Number(input.dataset.port || 0),
      path: input.dataset.builtin ? builtinPath(workspaceName, input.dataset.builtin) : (input.dataset.path || '/'),
      protocol: input.dataset.protocol || 'http',
    }))
    .filter(Boolean);
  return normalizePanels(tabs);
}

function setTerminalSession(profile, session) {
  const terminal = (profile.tabs || profile.panels || []).find((p) => p.label === 'Terminal' || p.service === 'terminal');
  if (!terminal) return;
  if (session === '1') {
    delete terminal.path;
  } else {
    terminal.path = `/?arg=${encodeURIComponent(session)}`;
  }
}

function serviceSnapshotForServer(server) {
  const host = sessionMachineHost(server) || server.ip || '127.0.0.1';
  return state.serviceSnapshotByHost[host] || null;
}

function resolveTabEndpoint(server, tab) {
  const host = sessionMachineHost(server) || server.ip || '127.0.0.1';
  const snapshot = serviceSnapshotForServer(server);
  if (tab.service && snapshot && Array.isArray(snapshot.services)) {
    const candidates = snapshot.services.filter((service) => service.name === tab.service);
    const match = candidates.find((service) => service.sessionName === server.name)
      || candidates.find((service) => tab.port && service.port === tab.port)
      || candidates[0];
    if (match) {
      return {
        host,
        port: match.port,
        path: tab.path || match.path || '/',
        protocol: tab.protocol || match.protocol || 'http',
        embeddable: match.embeddable !== false && !tab.new_tab,
        source: 'live-service',
      };
    }
  }
  if (tab.port) {
    return {
      host,
      port: tab.port,
      path: tab.path || '/',
      protocol: tab.protocol || window.location.protocol.replace(':', ''),
      embeddable: tab.service !== 'hub' && !tab.new_tab,
      source: 'saved-fallback',
    };
  }
  return null;
}

function gatewayUrl(host, port, path) {
  const target = isLocalHost(host) ? state.hubHost : host;
  const p = path && path.startsWith('/') ? path : `/${path || ''}`;
  return `/m/${encodeURIComponent(target)}/${port}${p}`;
}

function panelUrl(protocol, host, port, path) {
  if (state.viaGateway) return gatewayUrl(host, port, path);
  return `${protocol}://${resolveHost(host)}:${port}${path}`;
}

function endpointUrl(server, tab, endpoint) {
  const path = endpoint?.path || tab.path || '/';
  const protocol = endpoint?.protocol || tab.protocol || window.location.protocol.replace(':', '');
  const port = endpoint?.port || tab.port;
  return port ? panelUrl(protocol, endpoint?.host || sessionMachineHost(server), port, path) : 'about:blank';
}

function ensureFrame(server, tab, endpoint) {
  const framesEl = document.getElementById('frames');
  const id = frameIdFor(server, tab, endpoint);
  const existing = document.getElementById(id);
  if (existing) return existing;

  const url = endpointUrl(server, tab, endpoint);

  if (endpoint?.embeddable === false) {
    const notice = document.createElement('div');
    notice.id = id;
    notice.className = 'frame-notice';

    const card = document.createElement('div');
    card.className = 'frame-card';

    const heading = document.createElement('h3');
    heading.textContent = tab.label;

    const desc = document.createElement('p');
    desc.textContent = 'This page should be opened directly instead of inside the control iframe.';

    const shown = document.createElement('code');
    shown.className = 'frame-url';
    shown.textContent = url;

    const actions = document.createElement('div');
    actions.className = 'frame-actions';

    const link = document.createElement('a');
    link.className = 'frame-link';
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'Open in New Tab';

    actions.appendChild(link);
    card.appendChild(heading);
    card.appendChild(desc);
    card.appendChild(shown);
    card.appendChild(actions);
    notice.appendChild(card);
    framesEl.appendChild(notice);
    return notice;
  }

  const iframe = document.createElement('iframe');
  iframe.id = id;
  iframe.allow = 'clipboard-read; clipboard-write';
  iframe.src = url;
  framesEl.appendChild(iframe);
  return iframe;
}

function activateFrame(frameId) {
  document.querySelectorAll('#frames iframe, #frames .frame-notice').forEach((f) => f.classList.remove('active'));
  const frame = document.getElementById(frameId);
  if (frame) frame.classList.add('active');
}

async function selectSession(server) {
  state.activeSession = server.name;
  saveUIState();
  renderSidebar();
  updateSidebarColor();
  await renderTabs(server);
}

async function reloadSessions(preferredName) {
  renderSidebar();
  if (!state.sessions.length) {
    activateFrame('');
    return;
  }
  const target = state.sessions.find((s) => s.name === preferredName)
    || state.sessions.find((s) => s.name === state.activeSession)
    || state.sessions[0];
  await selectSession(target);
}

// Show a workspace's Terminal tab, switched to the given tmux window (an id like @12).
async function focusWorkspace(name, tmuxWindow) {
  const server = state.sessions.find((s) => s.name === name);
  if (!server) return false;
  const terminal = (server.tabs || server.panels || []).find((t) => t.service === 'terminal');
  if (terminal) state.activeTabBySession[name] = terminal.id || terminal.label;
  if (tmuxWindow) {
    const session = new URLSearchParams(terminal?.path?.split('?')[1] || '').get('arg') || name;
    await fetch('/tmux/select-window', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ session, window: tmuxWindow }),
    }).catch(() => {});
  }
  await selectSession(server);
  return true;
}

// /?session=<name>&window=<id> lands on that terminal window; /?view=latest (what
// /recent redirects to) opens the sessions view. Session params are dropped so a
// reload doesn't switch windows again.
async function applyDeepLink() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('view') === 'latest') {
    window.history.replaceState({ rrView: 'latest-entry' }, '', '/?view=latest');
    showLatest();
    return false;
  }
  if (!params.has('session')) return false;
  window.history.replaceState(null, '', window.location.pathname);
  return focusWorkspace(params.get('session'), params.get('window'));
}

// ── Latest Claude sessions ────────────────────────────────────────────────────
let latestTimer = null;
let latestRows = [];
let latestPriorities = {};  // Claude session id -> P0..P2
let latestHost = '';

// "Done" = went idle after you last saw it. Seen = clicked here, or its tmux window
// is the one showing on an attached screen. Stored per browser, by Claude session id.
const SEEN_KEY = 'rr_claude_seen';
const latestSeen = (() => {
  try {
    const parsed = JSON.parse(localStorage.getItem(SEEN_KEY) || 'null');
    if (parsed && typeof parsed.since === 'number' && parsed.at) return parsed;
  } catch (_) {}
  // First use: only turns finished in the last 30 minutes count as unseen.
  return { since: Date.now() - 30 * 60 * 1000, at: {} };
})();

const VIEW_KEY = 'rr_latest_view';
const STATE_KEYS = ['needs', 'done', 'idle', 'working'];
const latestPrefs = (() => {
  const prefs = { sort: 'triage', states: [...STATE_KEYS], prio: 'all' };
  try {
    const saved = JSON.parse(localStorage.getItem(VIEW_KEY) || 'null') || {};
    if (['triage', 'priority', 'recent', 'project'].includes(saved.sort)) prefs.sort = saved.sort;
    if (Array.isArray(saved.states)) prefs.states = saved.states.filter((k) => STATE_KEYS.includes(k));
    if (['all', '0', '1'].includes(saved.prio)) prefs.prio = saved.prio;
  } catch (_) {}
  return prefs;
})();

function saveLatestPrefs() {
  try { localStorage.setItem(VIEW_KEY, JSON.stringify(latestPrefs)); } catch (_) {}
}

function markSeen(sessionId) {
  latestSeen.at[sessionId] = Date.now();
}

function saveSeen(rows) {
  const live = new Set(rows.map((row) => row.session_id));
  Object.keys(latestSeen.at).forEach((id) => { if (!live.has(id)) delete latestSeen.at[id]; });
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(latestSeen)); } catch (_) {}
}

const WAITING_LABELS = { 'permission prompt': 'permission', 'input needed': 'question' };

function latestState(row) {
  if (row.status === 'waiting') {
    return { key: 'needs', label: 'needs you', title: row.waiting_for || 'waiting for you',
      detail: WAITING_LABELS[row.waiting_for] || row.waiting_for };
  }
  if (row.status === 'busy') return { key: 'working', label: 'working' };
  if (row.status === 'idle' && row.status_since > (latestSeen.at[row.session_id] ?? latestSeen.since)) {
    return { key: 'done', label: 'done', title: 'finished since you last looked' };
  }
  return null;
}

function stateKey(row) {
  return latestState(row)?.key || 'idle';
}

// Triage order. Working first (hide them with the Working chip), then what needs you.
const STATE_RANK = { working: 0, needs: 1, done: 2, idle: 3 };
const PRIO_RANK = { P0: 0, P1: 1, P2: 2 };

// Set in the workspace's settings (⚙). Read live so an edit there shows at once.
function projectPriority(row) {
  const workspace = row.workspace && state.sessions.find((s) => s.name === row.workspace);
  return workspace ? workspace.priority : row.workspace_priority || undefined;
}

// A session's own priority wins; otherwise it inherits its workspace's.
function effectivePriority(row) {
  return latestPriorities[row.session_id] || projectPriority(row);
}

// Rows with somewhere to go: a tmux window, or a background session we can attach.
function canOpen(row) {
  return Boolean(row.workspace && state.sessions.some((s) => s.name === row.workspace)
    && (row.tmux_window || row.background));
}

function prioRank(priority) {
  return PRIO_RANK[priority] ?? 3;
}

function compareBy(...keys) {
  return (a, b) => {
    for (const key of keys) {
      const x = key(a);
      const y = key(b);
      if (x < y) return -1;
      if (x > y) return 1;
    }
    return 0;
  };
}

function sortLatest(rows) {
  const state = (row) => STATE_RANK[stateKey(row)];
  const prio = (row) => prioRank(effectivePriority(row));
  const newest = (row) => -row.last_active;
  const openable = (row) => (canOpen(row) ? 0 : 1);  // dead ends always sink to the bottom
  const project = (row) => (row.workspace ? row.workspace_display.toLowerCase() : '￿');
  const projectPrio = (row) => prioRank(projectPriority(row));
  const orders = {
    triage: [openable, state, prio, newest],
    priority: [openable, prio, state, newest],
    recent: [openable, newest],
    project: [openable, projectPrio, project, state, prio, newest],
  };
  return [...rows].sort(compareBy(...orders[latestPrefs.sort]));
}

function filterLatest(rows) {
  const states = new Set(latestPrefs.states);
  const maxRank = latestPrefs.prio === 'all' ? 3 : Number(latestPrefs.prio);
  return rows.filter((row) => states.has(stateKey(row)) && prioRank(effectivePriority(row)) <= maxRank);
}

function timeAgo(ms) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function latestEl(tag, className, text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text) node.textContent = text;
  return node;
}

function workspaceColor(name) {
  const workspace = name && state.sessions.find((s) => s.name === name);
  return workspace ? colorForSession(workspace) : '';
}

async function saveSessionPriority(sessionId, priority) {
  if (priority) latestPriorities[sessionId] = priority;
  else delete latestPriorities[sessionId];
  renderLatest();
  try {
    const response = await fetch('/claude/priorities', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, priority: priority || null }),
    });
    if (response.ok) latestPriorities = (await response.json()).priorities;
  } catch (_) {}
  renderLatest();
}

// While a priority menu is open the list must not be rebuilt (that would close it).
// A <select> keeps focus after a choice, so the pause ends on change/blur and is
// capped, or one pick would freeze the live view.
let latestPickerSince = 0;
const PICKER_PAUSE_MS = 15000;

function pickerOpen() {
  return latestPickerSince > 0 && Date.now() - latestPickerSince < PICKER_PAUSE_MS;
}

// <select> for P0/P1/P2. `inherited` is what an empty value falls back to.
function prioritySelect(value, inherited, onChange) {
  const select = latestEl('select', 'prio-select');
  const shown = value || inherited;
  if (shown) select.classList.add(shown.toLowerCase());
  if (!value && inherited) select.classList.add('inherited');
  const blank = inherited ? `${inherited} (workspace)` : '–';
  [['', blank], ['P0', 'P0'], ['P1', 'P1'], ['P2', 'P2']].forEach(([v, label]) => {
    const option = document.createElement('option');
    option.value = v;
    option.textContent = label;
    option.selected = v === (value || '');
    select.append(option);
  });
  select.onfocus = () => { latestPickerSince = Date.now(); };
  select.onblur = () => { latestPickerSince = 0; };
  select.onchange = () => {
    latestPickerSince = 0;
    select.blur();
    onChange(select.value || null);
  };
  return select;
}

// A background session has no window yet: the hub opens (or reuses) one running
// `claude attach <id>` in its workspace's tmux session, then we jump there.
async function openLatestRow(row) {
  let tmuxWindow = row.tmux_window;
  if (row.background) {
    try {
      const response = await fetch('/claude/attach', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ session_id: row.session_id }),
      });
      if (!response.ok) throw new Error(String(response.status));
      tmuxWindow = (await response.json()).window;
    } catch (_) {
      alert(`Couldn't open background session "${row.title || row.session_id}" in a terminal.`);
      return;
    }
  }
  await focusWorkspace(row.workspace, tmuxWindow);
}

function buildLatestRow(row) {
  const linked = canOpen(row);
  const node = latestEl('div', 'latest-row');
  const color = workspaceColor(row.workspace);
  if (color) node.style.setProperty('--session-color', color);
  if (!linked) node.classList.add('unlinked');

  const main = latestEl(linked ? 'button' : 'div', 'row-main');
  if (linked) {
    main.type = 'button';
    main.onclick = async () => {
      markSeen(row.session_id);
      saveSeen(latestRows);
      closeLatest();
      await openLatestRow(row);
    };
  }

  const when = latestEl('span', 'when');
  when.append(latestEl('span', 'ago', timeAgo(row.last_active)));
  when.title = new Date(row.last_active).toLocaleString();
  const rowState = latestState(row);
  if (rowState) {
    node.classList.add(`state-${rowState.key}`);
    const badge = latestEl('span', `state state-${rowState.key}`, rowState.label);
    if (rowState.title) badge.title = rowState.title;
    when.append(badge);
    if (rowState.detail) when.append(latestEl('span', 'state-detail', rowState.detail));
  }
  main.append(when, latestEl('span', 'title', row.title || row.name || row.session_id));

  const folder = row.cwd.split('/').filter(Boolean).pop() || row.cwd;
  let where;
  if (row.background) {
    where = row.workspace
      ? `background · ${folder} · opens in ${row.workspace_display}`
      : `background · ${folder} · no workspace for this folder, use: claude attach ${row.job_id}`;
  } else if (row.workspace) {
    where = `${row.workspace_display} › ${row.tmux_window_name} · ${folder}`;
  } else {
    where = `${folder} · ${row.tmux_session ? `tmux ${row.tmux_session}, no workspace` : 'not in tmux, nothing to open'}`;
  }
  main.append(latestEl('span', 'where', where));
  if (row.last_prompt) main.append(latestEl('span', 'prompt', `› ${row.last_prompt}`));

  const prio = latestEl('div', 'row-prio');
  const select = prioritySelect(latestPriorities[row.session_id], projectPriority(row),
    (value) => saveSessionPriority(row.session_id, value));
  select.title = 'Session priority';
  prio.append(select);
  node.append(main, prio);
  return node;
}

function renderLatestControls() {
  document.querySelectorAll('#latest-sort button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.sort === latestPrefs.sort)));
  document.querySelectorAll('#latest-states button').forEach((b) => b.setAttribute('aria-pressed', String(latestPrefs.states.includes(b.dataset.state))));
  document.querySelectorAll('#latest-prio button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.prio === latestPrefs.prio)));
}

function renderLatest() {
  const view = document.getElementById('latest-view');
  if (!view.classList.contains('open')) return;
  if (pickerOpen()) return;
  renderLatestControls();

  const rows = filterLatest(sortLatest(latestRows));
  const items = [];
  let group = null;
  rows.forEach((row) => {
    if (latestPrefs.sort === 'project' && row.workspace !== group) {
      group = row.workspace;
      const head = latestEl('li', 'latest-group', row.workspace ? row.workspace_display : 'No workspace');
      const color = workspaceColor(row.workspace);
      if (color) head.style.setProperty('--session-color', color);
      items.push(head);
    }
    const item = document.createElement('li');
    item.append(buildLatestRow(row));
    items.push(item);
  });
  if (!items.length && latestRows.length) items.push(latestEl('li', 'latest-empty', 'No session matches these filters.'));
  document.getElementById('latest-list').replaceChildren(...items);

  const counts = latestCounts();
  const parts = [];
  if (counts.needs) parts.push(`${counts.needs} need you`);
  if (counts.working) parts.push(`${counts.working} working`);
  if (counts.done) parts.push(`${counts.done} done`);
  const shown = rows.length === latestRows.length ? `${latestRows.length}` : `${rows.length} of ${latestRows.length}`;
  document.getElementById('latest-meta').textContent = [...parts, `${shown} running on ${latestHost}`].join(' · ');
}

// Only rows you can open count toward the ◷ badge and the header summary.
function latestCounts() {
  const counts = { needs: 0, working: 0, done: 0, idle: 0 };
  latestRows.forEach((row) => { if (canOpen(row)) counts[stateKey(row)] += 1; });
  return counts;
}

function updateLatestBadge() {
  const counts = latestCounts();
  const btn = document.getElementById('latest-btn');
  btn.classList.toggle('has-needs', counts.needs > 0);
  btn.classList.toggle('has-done', counts.needs === 0 && counts.done > 0);
  document.getElementById('latest-badge').textContent = counts.needs > 0 ? String(counts.needs) : '';
  const parts = [];
  if (counts.needs) parts.push(`${counts.needs} need you`);
  if (counts.working) parts.push(`${counts.working} working`);
  if (counts.done) parts.push(`${counts.done} done`);
  btn.title = `Claude sessions${parts.length ? ` — ${parts.join(', ')}` : ''}`;
}

// Polls even while the view is closed, so the ◷ button can say when a session needs you.
async function refreshLatest() {
  const errorEl = document.getElementById('latest-error');
  try {
    const response = await fetch('/claude/sessions');
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    latestRows = data.sessions;
    latestPriorities = data.priorities || latestPriorities;
    latestHost = data.host;
    latestRows.forEach((row) => { if (row.on_screen) markSeen(row.session_id); });
    saveSeen(latestRows);
    updateLatestBadge();
    renderLatest();
    errorEl.style.display = 'none';
    if (document.getElementById('latest-view').classList.contains('open')) refreshQueue();
  } catch (_) {
    errorEl.style.display = 'block';
  }
}

function startLatestPolling() {
  if (latestTimer !== null) return;
  refreshLatest();
  latestTimer = window.setInterval(refreshLatest, 5000);
}

function showLatest() {
  document.getElementById('latest-view').classList.add('open');
  renderLatest();
  renderQueue();
  refreshLatest();
}

function hideLatest() {
  document.getElementById('latest-view').classList.remove('open');
}

// The view is a history entry, so the browser's back button closes it.
function openLatest() {
  if (document.getElementById('latest-view').classList.contains('open')) return;
  window.history.pushState({ rrView: 'latest' }, '', '/?view=latest');
  showLatest();
}

function closeLatest() {
  if (!document.getElementById('latest-view').classList.contains('open')) return;
  if (window.history.state?.rrView === 'latest') {
    window.history.back();  // popstate hides the view
  } else {
    window.history.pushState(null, '', '/');
    hideLatest();
  }
}

// ---- New task (Start now / Queue) and the queue list --------------------------
let queueTasks = [];
let taskOptions = null;            // {machines, workspaces} from /claude/launch-options
let taskFolderTouched = false;     // folder typed/browsed by hand (sent with Queue)
let taskWorkspaceTouched = false;  // workspace picked by hand (sent with Queue; a pre-pick isn't)
let latestFlashTimer = null;

function latestFlash(text, isError = false) {
  const el = document.getElementById('latest-flash');
  el.textContent = text;
  el.classList.toggle('error', isError);
  el.classList.add('show');
  window.clearTimeout(latestFlashTimer);
  latestFlashTimer = window.setTimeout(() => el.classList.remove('show'), isError ? 9000 : 5000);
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: options.body ? { 'content-type': 'application/json' } : undefined,
  });
  if (response.status === 204) return null;
  let body = null;
  try { body = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error((body && body.detail && String(body.detail)) || `HTTP ${response.status}`);
  return body;
}

function taskEls() {
  const $ = (id) => document.getElementById(id);
  return {
    modal: $('task-modal'), form: $('task-card'), prompt: $('task-prompt'), name: $('task-name'), machine: $('task-machine'),
    workspace: $('task-workspace'), priority: $('task-priority'), folder: $('task-folder'),
    browse: $('task-browse'), dirs: $('task-dirs'), dirsPath: $('task-dirs-path'), dirsList: $('task-dirs-list'),
    autonomous: $('task-autonomous'), hint: $('task-hint'), error: $('task-error'),
    start: $('task-start'), queue: $('task-queue'), cancel: $('task-cancel'),
  };
}

function taskError(text) {
  const { error } = taskEls();
  error.textContent = text || '';
  error.classList.toggle('show', Boolean(text));
}

function fillTaskWorkspaces() {
  const els = taskEls();
  const machine = els.machine.value;
  const current = els.workspace.value;
  const rows = (taskOptions?.workspaces || []).filter((w) => !machine || w.machine === machine);
  const options = [['', machine ? 'Any (queue) / choose to start' : 'Choose…']]
    .concat(rows.map((w) => [w.name, w.display]));
  els.workspace.replaceChildren(...options.map(([value, label]) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    return option;
  }));
  els.workspace.value = rows.some((w) => w.name === current) ? current : '';
}

function selectedTaskWorkspace() {
  const name = taskEls().workspace.value;
  return (taskOptions?.workspaces || []).find((w) => w.name === name) || null;
}

function updateTaskHint() {
  const els = taskEls();
  const workspace = selectedTaskWorkspace();
  const machine = els.machine.value;
  const parts = [];
  if (workspace) {
    parts.push(`Start: new window in ${workspace.display} on ${workspace.machine}, in ${els.folder.value.trim() || workspace.folder || '~'}.`);
  } else {
    parts.push('Start: choose a workspace.');
  }
  if (machine) {
    let target = `any agent on ${machine}`;
    if (workspace && taskWorkspaceTouched) target += ` in ${workspace.display}`;
    if (taskFolderTouched && els.folder.value.trim()) target += `, in ${els.folder.value.trim()}`;
    parts.push(`Queue: for ${target}.`);
  } else {
    parts.push('Queue: choose a computer.');
  }
  els.hint.textContent = parts.join(' ');
  els.start.disabled = !workspace;
  els.queue.disabled = !machine;
}

function onTaskWorkspaceChange() {
  const els = taskEls();
  const workspace = selectedTaskWorkspace();
  if (workspace) {
    if (els.machine.value !== workspace.machine) {
      els.machine.value = workspace.machine;
      fillTaskWorkspaces();
      els.workspace.value = workspace.name;
    }
    if (!taskFolderTouched) els.folder.value = workspace.folder || '';
    els.folder.placeholder = workspace.folder || '~';
  } else if (!taskFolderTouched) {
    els.folder.value = '';
    els.folder.placeholder = '~';
  }
  els.dirs.hidden = true;
  updateTaskHint();
}

async function browseTaskDirs(path) {
  const els = taskEls();
  const machine = els.machine.value;
  if (!machine) { taskError('Choose a computer first.'); return; }
  taskError('');
  els.dirs.hidden = false;
  els.dirsPath.textContent = 'Loading…';
  els.dirsList.replaceChildren();
  try {
    const query = new URLSearchParams({ machine });
    if (path) query.set('path', path);
    const data = await apiJson(`/claude/dirs?${query}`);
    els.dirsPath.textContent = data.path;
    const items = [];
    const add = (label, cls, handler) => {
      const li = document.createElement('li');
      const button = latestEl('button', cls, label);
      button.type = 'button';
      button.onclick = handler;
      li.append(button);
      items.push(li);
    };
    add(`✓ Use ${data.path}`, 'use', () => {
      els.folder.value = data.path;
      taskFolderTouched = true;
      els.dirs.hidden = true;
      updateTaskHint();
    });
    if (data.parent) add('↑ ..', '', () => browseTaskDirs(data.parent));
    data.dirs.forEach((name) => add(`${name}/`, '', () => browseTaskDirs(`${data.path}/${name}`)));
    els.dirsList.replaceChildren(...items);
  } catch (err) {
    els.dirsPath.textContent = '';
    taskError(`Can't list folders: ${err.message}`);
  }
}

async function openTaskModal() {
  const els = taskEls();
  taskError('');
  taskFolderTouched = false;
  taskWorkspaceTouched = false;
  els.form.reset();
  els.dirs.hidden = true;
  els.modal.classList.add('open');
  els.prompt.focus();
  try {
    taskOptions = await apiJson('/claude/launch-options');
  } catch (err) {
    taskError(`Can't load computers and workspaces: ${err.message}`);
    return;
  }
  const local = taskOptions.machines.find((m) => m.local);
  els.machine.replaceChildren(...[['', 'Choose…'], ...taskOptions.machines.map((m) => [m.name, m.name])].map(([v, label]) => {
    const option = document.createElement('option');
    option.value = v;
    option.textContent = label;
    return option;
  }));
  els.machine.value = local ? local.name : '';
  fillTaskWorkspaces();
  // Pre-pick the workspace you're in, if it's on that computer.
  if (state.activeSession && taskOptions.workspaces.some((w) => w.name === state.activeSession && w.machine === els.machine.value)) {
    els.workspace.value = state.activeSession;
  }
  onTaskWorkspaceChange();
}

function closeTaskModal() {
  taskEls().modal.classList.remove('open');
}

function taskBusy(busy) {
  const els = taskEls();
  [els.start, els.queue].forEach((b) => { b.disabled = busy; });
  if (!busy) updateTaskHint();
}

async function submitTask(mode) {
  const els = taskEls();
  const prompt = els.prompt.value.trim();
  if (!prompt) { taskError('Write a prompt first.'); els.prompt.focus(); return; }
  const workspace = selectedTaskWorkspace();
  const priority = els.priority.value || null;
  const name = els.name.value.trim() || null;
  taskError('');
  taskBusy(true);
  try {
    if (mode === 'start') {
      if (!workspace) throw new Error('Choose a workspace to start in.');
      const result = await apiJson('/claude/start', {
        method: 'POST',
        body: JSON.stringify({
          workspace: workspace.name, prompt, name, priority,
          folder: els.folder.value.trim() || null, autonomous: els.autonomous.checked,
        }),
      });
      closeTaskModal();
      latestFlash(`Started in ${workspace.display} (${result.machine}), ${result.folder}.`);
      window.setTimeout(refreshLatest, 1500);
    } else {
      if (!els.machine.value) throw new Error('Choose a computer for the queue.');
      const result = await apiJson('/queue', {
        method: 'POST',
        body: JSON.stringify({
          prompt, name, priority, machine: els.machine.value,
          workspace: workspace && taskWorkspaceTouched ? workspace.name : null,
          folder: taskFolderTouched ? (els.folder.value.trim() || null) : null,
          autonomous: els.autonomous.checked, created_by: 'Sessions view',
        }),
      });
      closeTaskModal();
      latestFlash(`Queued for ${result.task.workspace ? `${result.task.machine} / ${workspace.display}` : `any agent on ${result.task.machine}`}${result.task.folder ? `, in ${result.task.folder}` : ''}.`);
      refreshQueue();
    }
  } catch (err) {
    taskError(err.message);
  } finally {
    taskBusy(false);
  }
}

function bindTaskForm() {
  const els = taskEls();
  document.getElementById('task-new').onclick = openTaskModal;
  els.cancel.onclick = closeTaskModal;
  els.modal.addEventListener('click', (e) => { if (e.target === els.modal) closeTaskModal(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && els.modal.classList.contains('open')) closeTaskModal();
  });
  els.machine.onchange = () => { fillTaskWorkspaces(); if (!els.workspace.value) taskWorkspaceTouched = false; onTaskWorkspaceChange(); };
  els.workspace.onchange = () => { taskWorkspaceTouched = Boolean(els.workspace.value); onTaskWorkspaceChange(); };
  els.folder.oninput = () => { taskFolderTouched = els.folder.value.trim() !== ''; updateTaskHint(); };
  els.browse.onclick = () => browseTaskDirs(els.folder.value.trim() || selectedTaskWorkspace()?.folder || null);
  els.form.onsubmit = (e) => { e.preventDefault(); submitTask('start'); };
  els.queue.onclick = () => submitTask('queue');
  els.prompt.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submitTask('start'); }
  });
}

async function refreshQueue() {
  try {
    const data = await apiJson('/queue');
    queueTasks = data.tasks || [];
    renderQueue();
  } catch (_) { /* the sessions refresh shows connectivity errors */ }
}

async function queueAction(task, method, suffix = '', body = undefined) {
  try {
    await apiJson(`/queue/${task.id}${suffix}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (err) {
    latestFlash(`Queue: ${err.message}`, true);
  }
  refreshQueue();
}

function buildQueueRow(task) {
  const node = latestEl('div', `queue-row ${task.status}`);
  const color = workspaceColor(task.workspace);
  if (color) node.style.setProperty('--session-color', color);

  const st = latestEl('div', 'qstate');
  const stuck = task.status === 'waiting' && task.stuck;
  const label = stuck ? 'stuck' : task.status;
  st.append(latestEl('span', `qpill ${stuck ? 'stuck' : task.status}`, label));
  const since = task.status === 'taken' ? task.taken_at : task.status === 'done' ? task.done_at : task.created_at;
  st.append(latestEl('span', '', timeAgo(since * 1000)));

  const main = latestEl('div', 'qmain');
  if (task.name) main.append(latestEl('strong', 'qname', task.name));
  const promptEl = latestEl('span', 'qprompt', task.prompt);
  promptEl.title = task.prompt;
  main.append(promptEl);
  const ws = task.workspace && state.sessions.find((s) => s.name === task.workspace);
  const where = [task.machine, ws ? displayName(ws) : task.workspace, task.folder].filter(Boolean).join(' › ');
  let whereText = `for ${where || 'any'}`;
  if (task.status === 'taken' && task.taken_by) {
    const who = task.taken_by.name || (task.taken_by.session_id || '').slice(0, 8) || 'an agent';
    whereText += ` · taken by ${who}${task.taken_by.pid ? '' : ' (no pid, 24h lease)'}`;
  }
  if (task.autonomous) whereText += ' · autonomous';
  main.append(latestEl('span', 'qwhere', whereText));
  if (task.note) main.append(latestEl('span', 'qnote', `note: ${task.note}`));
  if (stuck) main.append(latestEl('span', 'qnote', `given back ${task.requeues}×, not handed out until you unstick it`));

  const actions = latestEl('div', 'qactions');
  if (task.status === 'waiting') {
    const select = prioritySelect(task.priority, null, (value) => queueAction(task, 'PATCH', '', { priority: value }));
    select.title = 'Task priority';
    actions.append(select);
    if (stuck) {
      const unstick = latestEl('button', '', 'Unstick');
      unstick.type = 'button';
      unstick.onclick = () => queueAction(task, 'PATCH', '', { stuck: false });
      actions.append(unstick);
    }
    const del = latestEl('button', '', 'Delete');
    del.type = 'button';
    del.title = 'Cancel this task';
    del.onclick = () => { if (confirm('Delete this queued task?')) queueAction(task, 'DELETE'); };
    actions.append(del);
  } else if (task.priority) {
    actions.append(latestEl('span', `prio-select ${task.priority.toLowerCase()}`, task.priority));
  }
  node.append(st, main, actions);
  return node;
}

function renderQueue() {
  if (!document.getElementById('latest-view').classList.contains('open')) return;
  if (pickerOpen()) return;
  const dayAgo = Date.now() / 1000 - 24 * 3600;
  const live = queueTasks.filter((t) => t.status === 'waiting' || t.status === 'taken');
  const recent = queueTasks
    .filter((t) => t.status === 'done' && (t.done_at || 0) >= dayAgo)
    .sort((a, b) => b.done_at - a.done_at);
  const items = [...live, ...recent].map((task) => {
    const li = document.createElement('li');
    li.append(buildQueueRow(task));
    return li;
  });
  if (!items.length) items.push(latestEl('li', 'queue-empty', 'Nothing queued. Use + New task › Queue, or rr-queue add from an agent.'));
  document.getElementById('queue-list').replaceChildren(...items);
  const waiting = live.filter((t) => t.status === 'waiting').length;
  const taken = live.length - waiting;
  const parts = [];
  if (waiting) parts.push(`${waiting} waiting`);
  if (taken) parts.push(`${taken} taken`);
  if (recent.length) parts.push(`${recent.length} done today`);
  document.getElementById('queue-meta').textContent = parts.length ? `· ${parts.join(' · ')}` : '';
}

function bindLatestView() {
  document.getElementById('latest-btn').onclick = openLatest;
  document.getElementById('latest-back').onclick = closeLatest;
  window.addEventListener('popstate', () => {
    if (new URLSearchParams(window.location.search).get('view') === 'latest') showLatest();
    else hideLatest();
  });
  document.querySelectorAll('#latest-sort button').forEach((b) => {
    b.onclick = () => { latestPrefs.sort = b.dataset.sort; saveLatestPrefs(); renderLatest(); };
  });
  document.querySelectorAll('#latest-states button').forEach((b) => {
    b.onclick = () => {
      const on = new Set(latestPrefs.states);
      if (on.has(b.dataset.state)) on.delete(b.dataset.state);
      else on.add(b.dataset.state);
      latestPrefs.states = STATE_KEYS.filter((k) => on.has(k));
      saveLatestPrefs();
      renderLatest();
    };
  });
  document.querySelectorAll('#latest-prio button').forEach((b) => {
    b.onclick = () => { latestPrefs.prio = b.dataset.prio; saveLatestPrefs(); renderLatest(); };
  });
  bindTaskForm();
  startLatestPolling();
}

async function deleteSession(server) {
  if (!confirm(`Delete workspace "${displayName(server)}"?`)) return;
  state.pendingDeletedSessions.add(server.name);
  state.sessions = state.sessions.filter((s) => s.name !== server.name);
  saveProfiles();
  const fallback = state.activeSession === server.name ? null : state.activeSession;
  reloadSessions(fallback);
}

async function deleteSessionAndKill(server) {
  const session = terminalSessionForSession(server);
  const host = sessionMachineHost(server) || server.ip;
  const ok = confirm(
    `Delete workspace "${displayName(server)}" and try to kill tmux session "${session}" on ${host}?\n\n` +
    `This can stop running terminal tasks.`
  );
  if (!ok) return;

  try {
    const res = await fetch('/tmux/kill', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ host, session, port: 7000 }),
    }).then((r) => r.json());
    const status = res.status || 'unknown';
    if (status === 'killed') alert(`tmux session "${session}" killed on ${host}.`);
    else if (status === 'not_found') alert(`tmux session "${session}" not found on ${host}.`);
    else if (status === 'skipped') alert(`tmux kill skipped: ${res.reason || 'unknown'}`);
    else if (status === 'error') alert(`tmux kill error: ${res.reason || 'unknown'}`);
  } catch (err) {
    alert(`tmux kill request failed: ${err.message}`);
  }

  state.sessions = state.sessions.filter((s) => s.name !== server.name);
  state.pendingDeletedSessions.add(server.name);
  saveProfiles();
  const fallback = state.activeSession === server.name ? null : state.activeSession;
  reloadSessions(fallback);
}


function renderPanelEditorRows() {
  const rows = (state.panelEditor && state.panelEditor.panels) ? state.panelEditor.panels : [];
  panelList.innerHTML = '';
  rows.forEach((panel, idx) => {
    const row = document.createElement('div');
    row.className = 'panel-row';
    row.title = panel.service ? `Service-backed tab: ${panel.service}` : 'Custom tab';

    const label = document.createElement('input');
    label.type = 'text';
    label.value = panel.label || '';
    label.placeholder = 'Label';
    label.oninput = () => { panel.label = label.value; };

    const port = document.createElement('input');
    port.type = 'number';
    port.min = '1';
    port.max = '65535';
    port.value = String(panel.port || '');
    port.placeholder = 'Port';
    port.oninput = () => { panel.port = Number(port.value || 0); };

    const path = document.createElement('input');
    path.type = 'text';
    path.value = panel.path || '';
    path.placeholder = '/path';
    path.oninput = () => { panel.path = path.value; };

    const protocol = document.createElement('select');
    const p1 = document.createElement('option');
    p1.value = 'http';
    p1.textContent = 'http';
    const p2 = document.createElement('option');
    p2.value = 'https';
    p2.textContent = 'https';
    protocol.appendChild(p1);
    protocol.appendChild(p2);
    protocol.value = panel.protocol || 'http';
    protocol.onchange = () => { panel.protocol = protocol.value; };

    const newTab = document.createElement('label');
    newTab.className = 'panel-newtab';
    newTab.title = 'Open this page in a browser tab instead of inside Remote Rider (for pages that refuse to be framed)';
    const newTabBox = document.createElement('input');
    newTabBox.type = 'checkbox';
    newTabBox.checked = panel.new_tab === true;
    newTabBox.onchange = () => { panel.new_tab = newTabBox.checked ? true : undefined; };
    newTab.appendChild(newTabBox);
    newTab.appendChild(document.createTextNode('New tab'));

    const del = document.createElement('button');
    del.className = 'icon-btn';
    del.textContent = 'X';
    del.title = 'Remove panel';
    del.onclick = () => {
      state.panelEditor.panels.splice(idx, 1);
      renderPanelEditorRows();
    };

    row.appendChild(label);
    row.appendChild(port);
    row.appendChild(path);
    row.appendChild(protocol);
    row.appendChild(newTab);
    row.appendChild(del);
    panelList.appendChild(row);
  });
}

function applyDiscoveredServiceToEditor(service) {
  if (!state.panelEditor) return;
  const label = service.label || service.name;
  const existing = state.panelEditor.panels.find((panel) => panel.label === label);
  const nextPanel = {
    label,
    service: service.name || undefined,
    port: service.port,
    path: service.path || '/',
    protocol: service.protocol || 'http',
  };

  if (existing) {
    existing.service = nextPanel.service;
    existing.port = nextPanel.port;
    existing.path = nextPanel.path;
    existing.protocol = nextPanel.protocol;
  } else {
    state.panelEditor.panels.push(nextPanel);
  }

  renderPanelEditorRows();
}

function renderDiscoveredServices() {
  panelServices.innerHTML = '';
  const visibleServices = state.discoveredServices.filter((service) => !isLegacyWorkspaceToolService(service));

  if (!visibleServices.length) {
    const empty = document.createElement('div');
    empty.className = 'muted-note';
    empty.textContent = 'No services discovered yet.';
    panelServices.appendChild(empty);
    return;
  }

  visibleServices.forEach((service) => {
    const row = document.createElement('div');
    row.className = 'service-row';

    const main = document.createElement('div');
    main.className = 'service-main';

    const title = document.createElement('div');
    title.className = 'service-title';
    title.textContent = `${service.label} (${service.name}:${service.port})`;

    const meta = document.createElement('div');
    meta.className = 'service-meta';
    meta.textContent = `${service.protocol}://${service.host}:${service.port}${service.path || '/'}`;

    main.appendChild(title);
    main.appendChild(meta);

    const stateBadge = document.createElement('div');
    stateBadge.className = `service-state ${service.up ? 'up' : 'down'}`;
    stateBadge.textContent = service.up ? `up ${service.latency}ms` : 'down';
    if (service.error) stateBadge.title = service.error;

    const buttons = document.createElement('div');
    buttons.className = 'service-buttons';

    const useBtn = document.createElement('button');
    useBtn.className = 'secondary-btn';
    useBtn.type = 'button';
    if (service.embeddable === false) {
      useBtn.textContent = 'Open';
      useBtn.onclick = () => window.open(panelUrl(service.protocol, service.host, service.port, service.path || '/'), '_blank', 'noopener');
    } else {
      useBtn.textContent = 'Use as Tab';
      useBtn.onclick = () => applyDiscoveredServiceToEditor(service);
    }

    buttons.appendChild(useBtn);
    row.appendChild(main);
    row.appendChild(stateBadge);
    row.appendChild(buttons);
    panelServices.appendChild(row);
  });
}

async function refreshDiscoveredServices() {
  if (!state.panelEditor) return;
  const server = state.sessions.find((s) => s.name === state.panelEditor.name);
  if (!server) return;

  const host = sessionMachineHost(server) || server.ip || '127.0.0.1';
  panelServicesNote.textContent = `Discovering services on ${host}...`;
  const result = await fetchRemoteServices(host);
  state.serviceSnapshotByHost[host] = result;
  state.discoveredServices = result.services.map((service) => ({ ...service, host: result.host || host }));
  const visibleCount = state.discoveredServices.filter((service) => !isLegacyWorkspaceToolService(service)).length;
  panelServicesNote.textContent = result.error
    ? `Service discovery failed on ${result.host}: ${result.error}`
    : `Discovered ${visibleCount} service${visibleCount === 1 ? '' : 's'} on ${result.host}.`;
  renderDiscoveredServices();
}

async function openPanelModal(server) {
  state.panelEditor = {
    name: server.name,
    label: server.display || server.name,
    color: server.color || colorForSession(server),
    panels: normalizePanels(clonePanels(server)),
  };
  state.discoveredServices = [];
  panelTarget.textContent = sessionMachineHost(server) || server.ip || 'unknown-host';
  document.getElementById('panel-session-label').value = state.panelEditor.label;
  document.getElementById('panel-session-color').value = state.panelEditor.color;
  document.getElementById('panel-session-priority').value = server.priority || '';
  renderPanelEditorRows();
  renderDiscoveredServices();
  panelModal.classList.add('open');
  await refreshDiscoveredServices();
}

function closePanelModal() {
  panelModal.classList.remove('open');
  state.panelEditor = null;
  state.discoveredServices = [];
  panelServices.innerHTML = '';
  panelServicesNote.textContent = '';
}

async function syncPanelEditorFromRemote() {
  if (!state.panelEditor) return;
  const server = state.sessions.find((s) => s.name === state.panelEditor.name);
  if (!server) return;
  const host = sessionMachineHost(server) || server.ip;
  const remotePanels = await fetchMachinePanels(host);
  if (!remotePanels || !remotePanels.length) {
    alert(`Could not fetch remote panels from ${host}.`);
    return;
  }
  const session = terminalSessionForSession(server);
  state.panelEditor.panels = normalizePanels(remotePanels);
  const tempProfile = { tabs: state.panelEditor.panels };
  setTerminalSession(tempProfile, session);
  renderPanelEditorRows();
}

function savePanelEditor() {
  if (!state.panelEditor) return;
  const server = state.sessions.find((s) => s.name === state.panelEditor.name);
  if (!server) return;
  const newLabel = (document.getElementById('panel-session-label').value || '').trim();
  server.display = newLabel || server.name;
  server.color = state.panelEditor.color;
  // null, not undefined: the hub keeps a stored priority when the field is missing.
  server.priority = document.getElementById('panel-session-priority').value || null;
  server.tabs = normalizePanels(state.panelEditor.panels);
  server.panels = server.tabs;
  state.dirtySessions.add(server.name);
  saveProfiles();
  closePanelModal();
  reloadSessions(server.name);
}

async function refreshTmuxSessions(host) {
  const query = new URLSearchParams({ host: host || '127.0.0.1', port: '7000' }).toString();
  try {
    const payload = await fetch(`/tmux/sessions/proxy?${query}`).then((r) => r.json());
    state.tmuxSessions = Array.isArray(payload.sessions) ? payload.sessions : [];
  } catch (err) {
    state.tmuxSessions = [];
  }
}

function fillTmuxOptions(defaultSession) {
  addExistingSession.innerHTML = '';
  const sessions = state.tmuxSessions.slice();
  for (const s of sessions) {
    const opt = document.createElement('option');
    opt.value = s;
    opt.textContent = s;
    addExistingSession.appendChild(opt);
  }
  if (!sessions.length) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '(no existing tmux sessions found)';
    addExistingSession.appendChild(opt);
  }
  addExistingSession.value = sessions.includes(defaultSession) ? defaultSession : (sessions[0] || '');
}

function updateSessionSourceVisibility() {
  const source = document.querySelector('input[name="tmux-source"]:checked')?.value || 'existing';
  if (source === 'existing' && !state.tmuxSessions.length) {
    document.querySelector('input[name="tmux-source"][value="new"]').checked = true;
  }
  const effective = document.querySelector('input[name="tmux-source"]:checked')?.value || 'new';
  const usingExisting = effective === 'existing' && state.tmuxSessions.length > 0;
  addExistingWrap.classList.toggle('hidden', !usingExisting);
  addNewSessionWrap.classList.toggle('hidden', usingExisting);
}

function updateServerChoiceVisibility() {
  const isNew = addServerSelect.value === '__new__';
  addNewServerWrap.classList.toggle('hidden', !isNew);
  addKnownServerWrap.classList.toggle('hidden', !isNew);
}

function updateWorkspaceTypeVisibility() {
  const isSandbox = addWorkspaceType.value === 'sandbox';
  document.querySelectorAll('.normal-workspace-field').forEach((el) => {
    el.classList.toggle('hidden', isSandbox);
  });
  document.getElementById('add-submit').textContent = isSandbox ? 'Configure Sandbox' : 'Create Workspace';
}

function populateKnownServerOptions(defaultIp) {
  addKnownServer.innerHTML = '';
  const manual = document.createElement('option');
  manual.value = '';
  manual.textContent = 'Manual IP/host';
  addKnownServer.appendChild(manual);

  HOMELAB_SERVERS.forEach((server) => {
    const opt = document.createElement('option');
    opt.value = server.ip;
    opt.textContent = `${server.name} (${server.ip})`;
    addKnownServer.appendChild(opt);
  });

  const match = HOMELAB_SERVERS.find((s) => s.ip === defaultIp);
  addKnownServer.value = match ? match.ip : '';
}

async function populateServerChoices() {
  const current = state.sessions.find((s) => s.name === state.activeSession) || state.sessions[0];
  addServerSelect.innerHTML = '';

  if (current) {
    const currentHost = sessionMachineHost(current) || current.ip || 'unknown-host';
    const currentGroup = document.createElement('optgroup');
    currentGroup.label = 'Current Machine';
    const currentOpt = document.createElement('option');
    currentOpt.value = `current:${current.name}`;
    currentOpt.textContent = `Active workspace machine: ${displayName(current)} (${currentHost})`;
    currentGroup.appendChild(currentOpt);
    addServerSelect.appendChild(currentGroup);
  }

  const machineGroup = document.createElement('optgroup');
  machineGroup.label = 'Known Homelab Machines';
  HOMELAB_SERVERS.forEach((server) => {
    const opt = document.createElement('option');
    opt.value = `machine:${server.name}`;
    opt.textContent = `${server.name} (${server.ip})`;
    machineGroup.appendChild(opt);
  });
  addServerSelect.appendChild(machineGroup);

  const newOpt = document.createElement('option');
  newOpt.value = '__new__';
  newOpt.textContent = '+ Manual machine';
  addServerSelect.appendChild(newOpt);

  if (current) {
    const host = sessionMachineHost(current) || current.ip || '127.0.0.1';
    addServerSelect.value = `current:${current.name}`;
    addServerIp.value = host;
    addLabel.value = displayName(current);
    addProjectPath.value = current.project || `/home/dh/code/${displayName(current)}`;
    populateKnownServerOptions(host);
    await refreshTmuxSessions(host);
    await refreshWorkspaceTabOptions(host);
    const defaultSession = terminalSessionForSession(current);
    fillTmuxOptions(defaultSession);
    addNewSession.value = defaultSession === '1' ? 'job1' : `${defaultSession}-copy`;
  }
  if (!current) {
    const defaultMachine = HOMELAB_SERVERS[0];
    addServerSelect.value = defaultMachine ? `machine:${defaultMachine.name}` : '__new__';
    addServerIp.value = defaultMachine ? defaultMachine.ip : '';
    addLabel.value = defaultMachine ? defaultMachine.name : '';
    addProjectPath.value = `/home/dh/code/${addLabel.value || 'project'}`;
    populateKnownServerOptions(defaultMachine ? defaultMachine.ip : '');
    await refreshTmuxSessions(defaultMachine ? defaultMachine.ip : '127.0.0.1');
    await refreshWorkspaceTabOptions(defaultMachine ? defaultMachine.ip : '127.0.0.1');
    fillTmuxOptions('1');
    addNewSession.value = 'job1';
  }

  addWorkspaceType.value = 'normal';

  updateServerChoiceVisibility();
  updateSessionSourceVisibility();
  updateWorkspaceTypeVisibility();
}

async function openAddModal() {
  await populateServerChoices();
  addModal.classList.add('open');
}

function closeAddModal() {
  addModal.classList.remove('open');
}

function selectedTmuxSession() {
  const source = document.querySelector('input[name="tmux-source"]:checked')?.value || 'existing';
  const raw = source === 'existing' ? addExistingSession.value : addNewSession.value;
  return sanitizeSessionName(raw);
}

function selectedMachineNameSeed() {
  const selected = addServerSelect.value;
  const isNew = selected === '__new__';
  const label = (addLabel.value.trim() || 'workspace');
  if (isNew) return label;
  if (selected.startsWith('machine:')) {
    const machineName = selected.slice('machine:'.length);
    const knownMachine = HOMELAB_SERVERS.find((s) => s.name === machineName);
    return knownMachine ? knownMachine.name : 'workspace';
  }
  if (selected.startsWith('current:')) {
    const currentName = selected.slice('current:'.length);
    const selectedProfile = state.sessions.find((s) => s.name === currentName);
    return selectedProfile ? (selectedProfile.machine?.name || selectedProfile.name) : 'workspace';
  }
  return 'workspace';
}

function proposedWorkspaceName() {
  const session = selectedTmuxSession();
  const label = (addLabel.value.trim() || 'workspace');
  return makeUniqueName(`${selectedMachineNameSeed()}-${label}-${session}`);
}

async function submitAddModal() {
  const selected = addServerSelect.value;
  const isNew = selected === '__new__';
  if (addWorkspaceType.value === 'sandbox') {
    closeAddModal();
    await openSandboxModal();
    return;
  }
  const session = selectedTmuxSession();
  const label = (addLabel.value.trim() || 'workspace');
  const workspaceName = proposedWorkspaceName();

  let targetHost;
  let nameSeed = selectedMachineNameSeed();

  if (isNew) {
    targetHost = addServerIp.value.trim();
    if (!targetHost) {
      alert('Please set the server IP/host.');
      return;
    }
  } else if (selected.startsWith('machine:')) {
    const machineName = selected.slice('machine:'.length);
    const knownMachine = HOMELAB_SERVERS.find((s) => s.name === machineName);
    if (!knownMachine) return;
    targetHost = knownMachine.ip;
  } else if (selected.startsWith('current:')) {
    const currentName = selected.slice('current:'.length);
    const selectedProfile = state.sessions.find((s) => s.name === currentName);
    if (!selectedProfile) return;
    targetHost = sessionMachineHost(selectedProfile) || selectedProfile.ip;
  } else {
    return;
  }

  const tabs = buildWorkspaceTabs();
  const newProfile = {
    name: workspaceName,
    display: label,
    machine: {
      name: isNew ? label : nameSeed,
      host: targetHost,
    },
    ip: targetHost,
    project: addProjectPath.value.trim() || undefined,
    tabs,
  };

  setTerminalSession(newProfile, session);
  newProfile.panels = newProfile.tabs;

  state.sessions.unshift(newProfile);
  saveProfiles();
  closeAddModal();
  reloadSessions(newProfile.name);
}

let dragServerName = null;

function clearDropMarkers() {
  document.querySelectorAll('.session-row.drop-before, .session-row.drop-after').forEach((el) => {
    el.classList.remove('drop-before', 'drop-after');
  });
  document.querySelectorAll('.session-row.dragging').forEach((el) => {
    el.classList.remove('dragging');
  });
}

function moveServerTo(dragName, targetName, after) {
  if (!dragName || !targetName || dragName === targetName) return;
  const fromIdx = state.sessions.findIndex((s) => s.name === dragName);
  const targetIdx = state.sessions.findIndex((s) => s.name === targetName);
  if (fromIdx < 0 || targetIdx < 0) return;

  const [item] = state.sessions.splice(fromIdx, 1);
  let insertIdx = targetIdx;
  if (after) insertIdx += 1;
  if (fromIdx < targetIdx) insertIdx -= 1;
  state.sessions.splice(insertIdx, 0, item);
  saveProfiles();
}

async function closeTab(server, tab, endpoint) {
  const host = endpoint?.host || sessionMachineHost(server) || server.ip || '127.0.0.1';
  const port = endpoint?.port || tab.port;

  if (port) {
    try {
      await fetch('/services/stop/proxy', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ host, hub_port: 7000, port }),
      });
    } catch (_) {}
  }

  try {
    await fetch(`/sessions/${encodeURIComponent(server.name)}/tabs`, {
      method: 'DELETE',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ label: tab.label }),
    });
  } catch (_) {}

  const session = state.sessions.find((s) => s.name === server.name);
  if (session) {
    session.tabs = (session.tabs || []).filter((t) => t.id !== tab.id && t.label !== tab.label);
    session.panels = session.tabs;
    // The DELETE above already changed the server's authoritative copy; the
    // next poll/flush reconciles via the version token. Mark dirty so a rebase
    // keeps this removal, and cache the optimistic view so the UI doesn't
    // flicker before the next sync.
    state.dirtySessions.add(server.name);
    try { localStorage.setItem(PROFILES_KEY, JSON.stringify(state.sessions)); } catch (_) {}
  }

  const updatedServer = state.sessions.find((s) => s.name === server.name);
  if (updatedServer && state.activeSession === server.name) {
    await renderTabs(updatedServer, false);
  }
}

function renderSidebar() {
  const list = document.getElementById('session-list');
  list.innerHTML = '';

  state.sessions.forEach((server) => {
    const row = document.createElement('div');
    row.className = 'session-row' + (server.name === state.activeSession ? ' active' : '');
    row.dataset.session = server.name;
    row.style.setProperty('--session-color', colorForSession(server));

    row.addEventListener('dragover', (e) => {
      const source = dragServerName || e.dataTransfer.getData('text/plain');
      if (!source || source === server.name) return;
      e.preventDefault();
      const rect = row.getBoundingClientRect();
      const after = e.clientY > rect.top + rect.height / 2;
      row.classList.toggle('drop-before', !after);
      row.classList.toggle('drop-after', after);
    });

    row.addEventListener('dragleave', () => {
      row.classList.remove('drop-before', 'drop-after');
    });

    row.addEventListener('drop', (e) => {
      const source = dragServerName || e.dataTransfer.getData('text/plain');
      clearDropMarkers();
      if (!source || source === server.name) return;
      e.preventDefault();
      const rect = row.getBoundingClientRect();
      const after = e.clientY > rect.top + rect.height / 2;
      moveServerTo(source, server.name, after);
      renderSidebar();
    });

    const drag = document.createElement('button');
    drag.className = 'icon-btn drag-handle';
    drag.textContent = '::';
    drag.title = 'Drag to reorder';
    drag.draggable = true;
    drag.ondragstart = (e) => {
      dragServerName = server.name;
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', server.name);
    };
    drag.ondragend = () => {
      dragServerName = null;
      clearDropMarkers();
    };

    const main = document.createElement('button');
    main.className = 'session-main' + (server.name === state.activeSession ? ' active' : '');
    main.onclick = () => selectSession(server);

    const dot = document.createElement('span');
    dot.className = 'session-dot';

    const text = document.createElement('div');
    text.className = 'session-text';
    const label = document.createElement('div');
    label.className = 'session-label';
    label.textContent = displayName(server);
    const meta = document.createElement('div');
    meta.className = 'session-meta';
    meta.textContent = sessionMachineHost(server) || server.ip || 'unknown-host';
    text.appendChild(label);
    text.appendChild(meta);
    main.appendChild(dot);
    main.appendChild(text);

    const gear = document.createElement('button');
    gear.className = 'icon-btn';
    gear.textContent = '⚙';
    gear.title = 'Workspace setup';
    gear.onclick = (e) => { e.stopPropagation(); openPanelModal(server); };

    row.appendChild(drag);
    row.appendChild(main);
    row.appendChild(gear);
    list.appendChild(row);
  });
}

async function refreshServiceSnapshotForSession(server) {
  const host = sessionMachineHost(server) || server.ip || '127.0.0.1';
  const snapshot = await fetchRemoteServices(host);
  state.serviceSnapshotByHost[host] = snapshot;
  return snapshot;
}

async function renderTabs(server, refreshLive = true) {
  const tabs = document.getElementById('tabs');
  tabs.innerHTML = '';
  const sessionTabs = server.tabs || server.panels || [];

  if (!sessionTabs || sessionTabs.length === 0) {
    const addTabBtn = document.createElement('button');
    addTabBtn.id = 'add-tab-btn';
    addTabBtn.title = 'Add panel tab';
    addTabBtn.textContent = '+';
    addTabBtn.onclick = () => openPanelModal(server);
    tabs.appendChild(addTabBtn);
    delete state.activeTabBySession[server.name];
    saveUIState();
    activateFrame('');
    return;
  }

  const savedLabel = state.activeTabBySession[server.name];
  let selectedFrameId = null;
  let selectedLabel = null;

  sessionTabs.forEach((panel, i) => {
    const endpoint = resolveTabEndpoint(server, panel);
    const frame = ensureFrame(server, panel, endpoint);
    const frameId = frame.id;
    const btn = document.createElement('button');
    btn.className = 'tab-btn';
    const text = document.createElement('span');
    text.textContent = panel.label;
    btn.appendChild(text);
    const popoutBtn = document.createElement('span');
    popoutBtn.className = 'tab-popout-btn';
    popoutBtn.textContent = '↗';
    popoutBtn.title = `Open ${panel.label} in a new window (enables system clipboard)`;
    popoutBtn.onclick = (e) => {
      e.stopPropagation();
      const url = endpointUrl(server, panel, endpoint);
      window.open(url, '_blank', 'noopener,noreferrer');
    };
    btn.appendChild(popoutBtn);
    const isTerminal = (panel.service === 'terminal' || panel.label.toLowerCase() === 'terminal');
    if (!isTerminal) {
      const closeBtn = document.createElement('span');
      closeBtn.className = 'tab-close-btn';
      closeBtn.textContent = '×';
      closeBtn.title = `Close ${panel.label} and kill service`;
      closeBtn.onclick = async (e) => {
        e.stopPropagation();
        await closeTab(server, panel, endpoint);
      };
      btn.appendChild(closeBtn);
    }
    btn.onclick = () => {
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.activeTabBySession[server.name] = panel.id || panel.label;
      saveUIState();
      activateFrame(frameId);
    };
    tabs.appendChild(btn);

    if ((savedLabel && savedLabel === (panel.id || panel.label)) || (!savedLabel && i === 0)) {
      selectedFrameId = frameId;
      selectedLabel = panel.id || panel.label;
      btn.classList.add('active');
    }
  });

  if (!selectedFrameId) {
    const fallbackEndpoint = resolveTabEndpoint(server, sessionTabs[0]);
    selectedFrameId = frameIdFor(server, sessionTabs[0], fallbackEndpoint);
    selectedLabel = sessionTabs[0].id || sessionTabs[0].label;
  }

  const addTabBtn = document.createElement('button');
  addTabBtn.id = 'add-tab-btn';
  addTabBtn.title = 'Add panel tab';
  addTabBtn.textContent = '+';
  addTabBtn.onclick = () => openPanelModal(server);
  tabs.appendChild(addTabBtn);

  state.activeTabBySession[server.name] = selectedLabel;
  saveUIState();
  activateFrame(selectedFrameId);
  if (refreshLive) {
    refreshServiceSnapshotForSession(server).then(() => {
      if (state.activeSession === server.name) renderTabs(server, false);
    }).catch(() => {});
  }
}

// ── Sandbox ──────────────────────────────────────────────────────────────────

const SANDBOX_COLOR = '#1a7a3f';
const SANDBOX_GH_USER_KEY = 'sandbox-gh-user';
let _sandboxRepos = [];

async function _loadGithubRepos(username) {
  if (!username.trim()) return;
  try {
    const resp = await fetch(
      `https://api.github.com/users/${encodeURIComponent(username.trim())}/repos?per_page=100&type=owner&sort=updated`
    );
    if (!resp.ok) return;
    const data = await resp.json();
    _sandboxRepos = Array.isArray(data) ? data.map((r) => ({
      name: String(r.name || ''),
      cloneUrl: String(r.clone_url || ''),
      description: String(r.description || ''),
    })).filter((r) => r.name && r.cloneUrl) : [];
    _renderRepoSuggestions();
  } catch (_) {}
}

function _renderRepoSuggestions() {
  const input = document.getElementById('sandbox-repo');
  const list = document.getElementById('sandbox-repo-suggestions');
  if (!input || !list) return;

  const filter = input.value.toLowerCase().trim();
  const filtered = _sandboxRepos.filter((r) =>
    !filter || r.name.toLowerCase().includes(filter) || r.description.toLowerCase().includes(filter)
  ).slice(0, 14);

  list.innerHTML = '';
  if (!filtered.length) { list.classList.add('hidden'); return; }

  filtered.forEach((repo) => {
    const item = document.createElement('div');
    item.className = 'repo-suggestion-item';

    const nameEl = document.createElement('div');
    nameEl.className = 'repo-suggestion-name';
    nameEl.textContent = repo.name;
    item.appendChild(nameEl);

    if (repo.description) {
      const descEl = document.createElement('div');
      descEl.className = 'repo-suggestion-desc';
      descEl.textContent = repo.description;
      item.appendChild(descEl);
    }

    item.addEventListener('mousedown', (e) => {
      e.preventDefault();
      input.value = repo.cloneUrl;
      list.classList.add('hidden');
      // auto-suggest branch from repo name
      const branchInput = document.getElementById('sandbox-branch');
      if (branchInput && !branchInput.value.trim()) {
        branchInput.focus();
      }
    });

    list.appendChild(item);
  });

  list.classList.remove('hidden');
}

function closeSandboxModal() {
  document.getElementById('sandbox-modal').classList.remove('open');
}

async function openSandboxModal() {
  const machineSelect = document.getElementById('sandbox-machine');
  machineSelect.innerHTML = '';

  HOMELAB_SERVERS.forEach((server) => {
    const opt = document.createElement('option');
    opt.value = server.ip;
    opt.textContent = `${server.name} (${server.ip})`;
    machineSelect.appendChild(opt);
  });

  if (!machineSelect.options.length) {
    const opt = document.createElement('option');
    opt.value = '127.0.0.1';
    opt.textContent = 'localhost';
    machineSelect.appendChild(opt);
  }

  // Default to active workspace's machine.
  const active = state.sessions.find((s) => s.name === state.activeSession);
  if (active) {
    const host = sessionMachineHost(active) || active.ip || '';
    const match = HOMELAB_SERVERS.find((s) => s.ip === host);
    if (match) machineSelect.value = match.ip;
  }

  const ghUserInput = document.getElementById('sandbox-github-user');
  ghUserInput.value = localStorage.getItem(SANDBOX_GH_USER_KEY) || '';

  document.getElementById('sandbox-repo').value = '';
  document.getElementById('sandbox-repo-suggestions').classList.add('hidden');
  document.getElementById('sandbox-branch').value = '';
  document.getElementById('sandbox-status').textContent = '';
  document.getElementById('sandbox-submit').disabled = false;
  updateSandboxSourceVisibility();

  await refreshSandboxList(machineSelect.value);
  if (ghUserInput.value) _loadGithubRepos(ghUserInput.value);

  document.getElementById('sandbox-modal').classList.add('open');
  document.getElementById('sandbox-repo').focus();
}

async function refreshSandboxList(host) {
  const runningWrap = document.getElementById('sandbox-running-wrap');
  const runningList = document.getElementById('sandbox-running-list');
  const cloneWrap = document.getElementById('sandbox-existing-wrap');
  const cloneSelect = document.getElementById('sandbox-clone-from');
  runningList.innerHTML = '';
  cloneSelect.innerHTML = '';

  try {
    const query = new URLSearchParams({ host, hub_port: '7000' }).toString();
    const payload = await fetch(`/sandbox/list/proxy?${query}`).then((r) => r.json());
    const sandboxes = Array.isArray(payload.sandboxes) ? payload.sandboxes : [];

    if (sandboxes.length) {
      sandboxes.forEach((sb) => {
        const row = document.createElement('div');
        row.className = 'sandbox-running-row';

        const info = document.createElement('div');
        info.className = 'sandbox-running-info';
        const branch = document.createElement('div');
        branch.className = 'sandbox-running-branch';
        branch.textContent = sb.branch || sb.name;
        const meta = document.createElement('div');
        meta.className = 'sandbox-running-meta';
        meta.textContent = `${sb.status}${sb.repo ? '  ·  ' + sb.repo.split('/').slice(-1)[0] : ''}`;
        info.appendChild(branch);
        info.appendChild(meta);

        const openBtn = document.createElement('button');
        openBtn.className = 'secondary-btn';
        openBtn.type = 'button';
        openBtn.textContent = 'Select';
        openBtn.title = 'Jump to this workspace in the sidebar';
        openBtn.onclick = () => {
          const session = state.sessions.find((s) => {
            const t = (s.tabs || []).find((tab) => tab.port === sb.ttyd_port);
            return t && (sessionMachineHost(s) || s.ip) === host;
          });
          if (session) { closeSandboxModal(); selectSession(session); }
        };

        const stopBtn = document.createElement('button');
        stopBtn.className = 'danger-btn';
        stopBtn.type = 'button';
        stopBtn.textContent = 'Stop';
        stopBtn.onclick = async () => {
          stopBtn.disabled = true;
          stopBtn.textContent = '…';
          await fetch('/sandbox/stop/proxy', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ host, hub_port: 7000, container_id: sb.id }),
          });
          await refreshSandboxList(host);
        };

        row.appendChild(info);
        row.appendChild(openBtn);
        row.appendChild(stopBtn);
        runningList.appendChild(row);
      });
      runningWrap.classList.remove('hidden');

      const none = document.createElement('option');
      none.value = '';
      none.textContent = '— create fresh —';
      cloneSelect.appendChild(none);
      sandboxes.forEach((sb) => {
        const opt = document.createElement('option');
        opt.value = sb.id;
        opt.textContent = `${sb.branch || sb.name} (${sb.status})`;
        cloneSelect.appendChild(opt);
      });
      cloneWrap.classList.remove('hidden');
    } else {
      runningWrap.classList.add('hidden');
      cloneWrap.classList.add('hidden');
    }
  } catch (_) {
    runningWrap.classList.add('hidden');
    cloneWrap.classList.add('hidden');
  }
}

function updateSandboxSourceVisibility() {
  const source = document.querySelector('input[name="sandbox-source"]:checked')?.value || 'path';
  document.getElementById('sandbox-path-wrap').classList.toggle('hidden', source !== 'path');
  document.getElementById('sandbox-github-wrap').classList.toggle('hidden', source !== 'github');
}

async function submitSandboxModal() {
  const host = document.getElementById('sandbox-machine').value;
  const branch = document.getElementById('sandbox-branch').value.trim();
  const cloneFrom = document.getElementById('sandbox-clone-from').value;
  const source = document.querySelector('input[name="sandbox-source"]:checked')?.value || 'path';
  const repoUrl = source === 'github' ? document.getElementById('sandbox-repo').value.trim() : '';
  const localPath = source === 'path' ? document.getElementById('sandbox-local-path').value.trim() : '';
  const statusEl = document.getElementById('sandbox-status');
  const submitBtn = document.getElementById('sandbox-submit');

  if (!branch) { statusEl.textContent = 'Please enter a branch name.'; return; }
  if (source === 'github' && !repoUrl) { statusEl.textContent = 'Please enter a repository URL.'; return; }
  if (source === 'path' && !localPath) { statusEl.textContent = 'Please enter the repo path on the target machine.'; return; }

  statusEl.textContent = cloneFrom
    ? `Cloning sandbox to branch "${branch}"...`
    : `Creating sandbox — building image if needed, this may take a minute...`;
  submitBtn.disabled = true;

  let result;
  try {
    if (cloneFrom) {
      result = await fetch('/sandbox/clone/proxy', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ host, hub_port: 7000, container_id: cloneFrom, new_branch: branch }),
      }).then((r) => r.json());
    } else {
      result = await fetch('/sandbox/create/proxy', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ host, hub_port: 7000, repo_url: repoUrl, local_path: localPath, branch }),
      }).then((r) => r.json());
    }
  } catch (err) {
    statusEl.textContent = `Request failed: ${err.message}`;
    submitBtn.disabled = false;
    return;
  }

  if (result.status !== 'ok') {
    statusEl.textContent = `Error: ${result.reason || JSON.stringify(result)}`;
    submitBtn.disabled = false;
    return;
  }

  const shortBranch = branch.split('/').pop();
  const session = {
    name: makeUniqueName(`sandbox-${shortBranch}`),
    display: `⬡ ${shortBranch}`,
    color: SANDBOX_COLOR,
    machine: { name: `sandbox-${shortBranch}`, host },
    ip: host,
    tabs: [normalizeTab({
      label: 'Terminal',
      service: 'container',
      port: result.ttyd_port,
    })],
  };
  session.panels = session.tabs;

  state.sessions.unshift(session);
  saveProfiles();
  closeSandboxModal();
  reloadSessions(session.name);
}

async function init() {
  installViewportResizeHandling();
  loadUIState();
  await loadSessionTemplates();
  await loadProfilesFromBootstrap();

  document.getElementById('add-session-btn').onclick = openAddModal;
  document.getElementById('new-sandbox-btn').onclick = openSandboxModal;
  document.getElementById('update-remotes-btn').onclick = updateAllRemotes;
  document.getElementById('add-cancel').onclick = closeAddModal;
  document.getElementById('add-submit').onclick = submitAddModal;
  document.getElementById('panel-cancel').onclick = closePanelModal;
  document.getElementById('panel-save').onclick = savePanelEditor;
  document.getElementById('panel-sync').onclick = syncPanelEditorFromRemote;
  document.getElementById('panel-refresh-services').onclick = refreshDiscoveredServices;
  document.getElementById('panel-session-color').addEventListener('input', (e) => {
    if (!state.panelEditor) return;
    state.panelEditor.color = e.target.value;
    const row = document.querySelector(`[data-session="${CSS.escape(state.panelEditor.name)}"]`);
    if (row) row.style.setProperty('--session-color', e.target.value);
    updateSidebarColor();
  });
  document.getElementById('panel-delete').onclick = () => {
    if (!state.panelEditor) return;
    const server = state.sessions.find((s) => s.name === state.panelEditor.name);
    if (!server || !confirm(`Delete workspace "${displayName(server)}"?`)) return;
    closePanelModal();
    state.sessions = state.sessions.filter((s) => s.name !== server.name);
    saveProfiles();
    reloadSessions(state.activeSession === server.name ? null : state.activeSession);
  };
  document.getElementById('panel-kill').onclick = async () => {
    if (!state.panelEditor) return;
    const server = state.sessions.find((s) => s.name === state.panelEditor.name);
    if (!server) return;
    closePanelModal();
    await deleteSessionAndKill(server);
  };
  document.getElementById('update-close').onclick = closeUpdateModal;
  document.getElementById('update-modal').addEventListener('click', (e) => {
    if (e.target === document.getElementById('update-modal')) closeUpdateModal();
  });
  document.getElementById('send-keys-btn').onclick = toggleKeysMode;
  bindLatestView();
  document.getElementById('fullscreen-btn').onclick = toggleFullscreen;
  document.getElementById('sidebar-collapse').onclick = () => setSidebarCollapsed(true);
  document.getElementById('sidebar-reveal').onclick = () => setSidebarCollapsed(false);
  const rrMenuBtn = document.getElementById('rr-menu-btn');
  rrMenuBtn.onclick = (e) => {
    e.stopPropagation();
    setRrMenu(document.getElementById('rr-menu').classList.contains('hidden'));
  };
  document.getElementById('rr-menu').addEventListener('click', () => setRrMenu(false));
  document.addEventListener('click', (e) => {
    if (!document.getElementById('rr-menu-wrap').contains(e.target)) setRrMenu(false);
  });
  try { if (localStorage.getItem('rr_sidebar_collapsed')) setSidebarCollapsed(true); } catch (_) {}
  document.getElementById('sandbox-cancel').onclick = closeSandboxModal;
  document.getElementById('sandbox-submit').onclick = submitSandboxModal;
  document.getElementById('sandbox-modal').addEventListener('click', (e) => {
    if (e.target === document.getElementById('sandbox-modal')) closeSandboxModal();
  });
  document.getElementById('sandbox-machine').addEventListener('change', (e) => {
    refreshSandboxList(e.target.value);
  });
  document.querySelectorAll('input[name="sandbox-source"]').forEach((r) => {
    r.addEventListener('change', updateSandboxSourceVisibility);
  });
  document.getElementById('sandbox-repo').addEventListener('focus', _renderRepoSuggestions);
  document.getElementById('sandbox-repo').addEventListener('input', _renderRepoSuggestions);
  document.getElementById('sandbox-repo').addEventListener('blur', () => {
    setTimeout(() => {
      const list = document.getElementById('sandbox-repo-suggestions');
      if (list) list.classList.add('hidden');
    }, 150);
  });
  document.getElementById('sandbox-github-user').addEventListener('change', async (e) => {
    localStorage.setItem(SANDBOX_GH_USER_KEY, e.target.value.trim());
    await _loadGithubRepos(e.target.value);
  });
  document.getElementById('sandbox-fetch-repos').addEventListener('click', async () => {
    const username = document.getElementById('sandbox-github-user').value.trim();
    if (!username) return;
    localStorage.setItem(SANDBOX_GH_USER_KEY, username);
    await _loadGithubRepos(username);
    document.getElementById('sandbox-repo').focus();
  });
  document.getElementById('panel-add').onclick = () => {
    if (!state.panelEditor) return;
    state.panelEditor.panels.push({ label: 'Panel', port: 9000, path: '/', protocol: 'http' });
    renderPanelEditorRows();
  };

  addWorkspaceType.addEventListener('change', updateWorkspaceTypeVisibility);

  addServerSelect.addEventListener('change', async () => {
    updateServerChoiceVisibility();
    if (addServerSelect.value === '__new__') {
      const baseServer = state.sessions.find((s) => s.name === state.activeSession) || state.sessions[0];
      const suggestedHost = baseServer ? (sessionMachineHost(baseServer) || baseServer.ip || '') : '';
      addServerIp.value = suggestedHost;
      if (!addLabel.value.trim()) addLabel.value = baseServer ? displayName(baseServer) : '';
      addProjectPath.value = baseServer?.project || `/home/dh/code/${addLabel.value.trim() || 'project'}`;
      populateKnownServerOptions(suggestedHost);
      await refreshTmuxSessions(suggestedHost || '127.0.0.1');
      await refreshWorkspaceTabOptions(suggestedHost || '127.0.0.1');
      fillTmuxOptions('1');
    } else if (addServerSelect.value.startsWith('current:')) {
      const sessionName = addServerSelect.value.slice('current:'.length);
      const server = state.sessions.find((s) => s.name === sessionName);
      if (server) {
        const host = sessionMachineHost(server) || server.ip || '127.0.0.1';
        addLabel.value = displayName(server);
        addProjectPath.value = server.project || `/home/dh/code/${displayName(server)}`;
        addServerIp.value = host;
        populateKnownServerOptions(host);
        await refreshTmuxSessions(host);
        await refreshWorkspaceTabOptions(host);
        fillTmuxOptions(terminalSessionForSession(server));
      }
    } else {
      const machineName = addServerSelect.value.slice('machine:'.length);
      const machine = HOMELAB_SERVERS.find((s) => s.name === machineName);
      const host = machine ? machine.ip : '';
      addServerIp.value = host;
      addLabel.value = machine ? machine.name : addLabel.value;
      addProjectPath.value = `/home/dh/code/${addLabel.value.trim() || 'project'}`;
      populateKnownServerOptions(host);
      await refreshTmuxSessions(host || '127.0.0.1');
      await refreshWorkspaceTabOptions(host || '127.0.0.1');
      fillTmuxOptions('1');
    }
    updateSessionSourceVisibility();
  });

  addKnownServer.addEventListener('change', async () => {
    if (addKnownServer.value) {
      addServerIp.value = addKnownServer.value;
      const match = HOMELAB_SERVERS.find((s) => s.ip === addKnownServer.value);
      if (match && !addLabel.value.trim()) addLabel.value = match.name;
      if (!addProjectPath.value.trim()) addProjectPath.value = `/home/dh/code/${addLabel.value.trim() || 'project'}`;
      await refreshTmuxSessions(addKnownServer.value);
      await refreshWorkspaceTabOptions(addKnownServer.value);
      fillTmuxOptions('1');
      updateSessionSourceVisibility();
    }
  });

  document.querySelectorAll('input[name="tmux-source"]').forEach((radio) => {
    radio.addEventListener('change', updateSessionSourceVisibility);
  });

  addModal.addEventListener('click', (e) => {
    if (e.target === addModal) closeAddModal();
  });
  panelModal.addEventListener('click', (e) => {
    if (e.target === panelModal) closePanelModal();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && addModal.classList.contains('open')) closeAddModal();
    if (e.key === 'Escape' && panelModal.classList.contains('open')) closePanelModal();
    if (e.key === 'Escape' && document.getElementById('latest-view').classList.contains('open')) closeLatest();
    if (e.key === 'Escape' && document.getElementById('update-modal').classList.contains('open')) closeUpdateModal();
    if (e.key === 'Escape' && state.keysMode) setKeysMode(false);
    if (e.key === 'Escape' && document.getElementById('sandbox-modal').classList.contains('open')) closeSandboxModal();
  });

  if (!(await applyDeepLink())) await reloadSessions(state.activeSession);
  window.setInterval(() => {
    refreshSessionsFromControlIfChanged();
  }, 4000);
  // Reload/close right after an edit: send the queued save now (keepalive).
  window.addEventListener('pagehide', () => {
    if (!saveSessionsTimer) return;
    clearTimeout(saveSessionsTimer);
    saveSessionsTimer = null;
    flushSessionsToControl();
  });
}

init();
