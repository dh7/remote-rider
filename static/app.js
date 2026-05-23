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
  controlSessionsSignature: '',
  pendingDeletedSessions: new Set(),
};

const addModal = document.getElementById('add-modal');
const addWorkspaceType = document.getElementById('add-workspace-type');
const addServerSelect = document.getElementById('add-server-select');
const addNewServerWrap = document.getElementById('add-new-server-wrap');
const addKnownServerWrap = document.getElementById('add-known-server-wrap');
const addKnownServer = document.getElementById('add-known-server');
const addServerIp = document.getElementById('add-server-ip');
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
  try {
    localStorage.setItem(PROFILES_KEY, JSON.stringify(state.sessions));
  } catch (_) {}
  queueSessionSaveToControl();
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
      return [];
    }
    state.controlSessionsAvailable = true;
    return payload.sessions.map(normalizeProfile).filter(Boolean);
  } catch (_) {
    state.controlSessionsAvailable = false;
    return null;
  }
}

async function loadControlContext() {
  try {
    const response = await fetch('/control/context');
    if (!response.ok) return null;
    state.controlSessionsAvailable = true;
    const payload = await response.json();
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

async function flushSessionsToControl() {
  if (!state.controlSessionsAvailable) return;
  try {
    const latest = await loadSessionsFromControl();
    if (latest === null) return;
    let sessionsToSave = state.sessions;
    if (Array.isArray(latest)) {
      const localByName = new Map(state.sessions.map((session) => [session.name, session]));
      const merged = [];
      const seen = new Set();
      latest.forEach((remoteSession) => {
        if (!remoteSession || !remoteSession.name || state.pendingDeletedSessions.has(remoteSession.name)) return;
        merged.push(localByName.get(remoteSession.name) || remoteSession);
        seen.add(remoteSession.name);
      });
      state.sessions.forEach((localSession) => {
        if (!localSession || !localSession.name || seen.has(localSession.name)) return;
        if (state.pendingDeletedSessions.has(localSession.name)) return;
        merged.push(localSession);
      });
      sessionsToSave = merged;
    }
    await fetch('/sessions', {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ sessions: sessionsToSave }),
    });
    state.sessions = sessionsToSave;
    state.pendingDeletedSessions.clear();
    state.controlSessionsSignature = JSON.stringify(state.sessions);
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
  const sessions = await loadSessionsFromControl();
  if (!sessions) return;
  const signature = JSON.stringify(sessions);
  if (signature === state.controlSessionsSignature) return;
  state.sessions = sessions;
  state.controlSessionsSignature = signature;
  try {
    localStorage.setItem(PROFILES_KEY, JSON.stringify(state.sessions));
  } catch (_) {}
  await reloadSessions(state.activeSession);
}

function closeUpdateModal() {
  document.getElementById('update-modal').classList.remove('open');
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
  if (key === 'monitor') return 'monitor';
  if (key === 'logs') return 'logs';
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
    machine: {
      name: machineName,
      host,
    },
    ip: host,
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
  const controlSessions = controlContext ? controlContext.sessions : await loadSessionsFromControl();
  if (controlSessions && controlSessions.length) {
    state.sessions = controlSessions;
    state.controlSessionsSignature = JSON.stringify(state.sessions);
    try {
      localStorage.setItem(PROFILES_KEY, JSON.stringify(state.sessions));
    } catch (_) {}
    return;
  }

  try {
    const raw = localStorage.getItem(PROFILES_KEY);
    if (!raw) {
      state.sessions = normalizedBootstrap;
      saveProfiles();
      return;
    }
    const parsed = JSON.parse(raw);
    const localProfiles = Array.isArray(parsed) ? parsed.map(normalizeProfile).filter(Boolean) : [];
    state.sessions = localProfiles.length ? localProfiles : normalizedBootstrap;
    state.controlSessionsSignature = JSON.stringify(state.sessions);
    if (controlSessions && controlSessions.length === 0 && state.sessions.length) {
      queueSessionSaveToControl();
    }
  } catch (_) {
    state.sessions = normalizedBootstrap;
    state.controlSessionsSignature = JSON.stringify(state.sessions);
    if (controlSessions && controlSessions.length === 0 && state.sessions.length) {
      queueSessionSaveToControl();
    }
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

async function probePanelStatus(server, tab, endpoint) {
  const host = endpoint?.host || sessionMachineHost(server) || server.ip || '127.0.0.1';
  const port = endpoint?.port || tab.port;
  if (!port) return { up: false, latency: 0, error: 'missing port', host };
  const query = new URLSearchParams({ host, port: String(port), timeout_ms: '650' }).toString();
  try {
    const payload = await fetch(`/panel/status?${query}`).then((r) => r.json());
    return {
      up: Boolean(payload.up),
      latency: Number(payload.latency_ms || 0),
      error: payload.error ? String(payload.error) : '',
      host,
    };
  } catch (err) {
    return { up: false, latency: 0, error: String(err.message || err), host };
  }
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
        { label: 'Monitor', port: 8001 },
        { label: 'Logs', port: 8002 },
        { label: 'Files', port: 8080, path: '/files' },
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

const DEFAULT_WORKSPACE_TABS = [
  { key: 'terminal', label: 'Terminal', service: 'terminal', port: 7681, path: '/', checked: true, required: true },
  { key: 'notes', label: 'Notes', service: 'notes', port: 8126, path: '/', checked: true },
  { key: 'skills', label: 'Skills', service: 'skills', port: 8129, path: '/', checked: true },
  { key: 'files', label: 'Files', service: 'files', port: 8080, path: '/files', checked: true },
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

function renderWorkspaceTabOptions(services = []) {
  addTabOptions.innerHTML = '';
  const used = new Set();
  const choices = DEFAULT_WORKSPACE_TABS.map((fallback) => {
    const service = findService(services, [fallback.service], [fallback.label]);
    used.add(String(fallback.service).toLowerCase());
    used.add(String(fallback.label).toLowerCase());
    return service ? tabChoiceFromService(service, fallback) : { ...fallback, protocol: 'http' };
  });

  services
    .filter((service) => service.embeddable !== false)
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
    label.appendChild(input);
    label.appendChild(document.createTextNode(choice.label));
    addTabOptions.appendChild(label);
  });
}

async function refreshWorkspaceTabOptions(host) {
  renderWorkspaceTabOptions();
  const snapshot = await fetchRemoteServices(host);
  renderWorkspaceTabOptions(snapshot.services || []);
}

function buildWorkspaceTabs() {
  const tabs = Array.from(addTabOptions.querySelectorAll('input[type="checkbox"]'))
    .filter((input) => input.checked || input.disabled)
    .map((input) => normalizeTab({
      label: input.dataset.label,
      service: input.dataset.service,
      port: Number(input.dataset.port || 0),
      path: input.dataset.path || '/',
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
        embeddable: match.embeddable !== false,
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
      embeddable: tab.service !== 'hub',
      source: 'saved-fallback',
    };
  }
  return null;
}

function endpointUrl(server, tab, endpoint) {
  const path = endpoint?.path || tab.path || '/';
  const protocol = endpoint?.protocol || tab.protocol || window.location.protocol.replace(':', '');
  const host = resolveHost(endpoint?.host || sessionMachineHost(server));
  const port = endpoint?.port || tab.port;
  return port ? `${protocol}://${host}:${port}${path}` : 'about:blank';
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
    card.appendChild(actions);
    notice.appendChild(card);
    framesEl.appendChild(notice);
    return notice;
  }

  const iframe = document.createElement('iframe');
  iframe.id = id;
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

  if (!state.discoveredServices.length) {
    const empty = document.createElement('div');
    empty.className = 'muted-note';
    empty.textContent = 'No services discovered yet.';
    panelServices.appendChild(empty);
    return;
  }

  state.discoveredServices.forEach((service) => {
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
      useBtn.onclick = () => window.open(`${service.protocol}://${service.host}:${service.port}${service.path || '/'}`, '_blank', 'noopener');
    } else {
      useBtn.textContent = 'Use as Tab';
      useBtn.onclick = () => applyDiscoveredServiceToEditor(service);
    }

    buttons.appendChild(useBtn);
    if (service.launchable && service.label === 'Files') {
      const launchBtn = document.createElement('button');
      launchBtn.className = 'secondary-btn';
      launchBtn.type = 'button';
      launchBtn.textContent = 'Launch New';
      launchBtn.onclick = () => startFilesServiceForEditor(null);
      buttons.appendChild(launchBtn);
    }
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
  panelServicesNote.textContent = result.error
    ? `Service discovery failed on ${result.host}: ${result.error}`
    : `Discovered ${state.discoveredServices.length} service${state.discoveredServices.length === 1 ? '' : 's'} on ${result.host}.`;
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

async function startFilesServiceForEditor(requestedPort = null) {
  if (!state.panelEditor) return;
  const server = state.sessions.find((s) => s.name === state.panelEditor.name);
  if (!server) return;

  const host = sessionMachineHost(server) || server.ip || '127.0.0.1';
  const payload = {
    host,
    hub_port: 7000,
    port: requestedPort,
  };

  let res;
  try {
    res = await fetch('/services/files/start/proxy', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    }).then((r) => r.json());
  } catch (err) {
    alert(`Failed to start files service: ${err.message}`);
    return;
  }

  if (!res || res.status !== 'ok' || !res.service || !res.service.port) {
    alert(`Could not start files service on ${host}: ${res?.reason || 'unknown error'}`);
    return;
  }

  const filesPort = Number(res.service.port);
  const filesPanel = state.panelEditor.panels.find((p) => p.label === 'Files');
  if (filesPanel) {
    filesPanel.service = 'files';
    filesPanel.port = filesPort;
    filesPanel.path = '/files';
    filesPanel.protocol = 'http';
  } else {
    state.panelEditor.panels.push({ label: 'Files', service: 'files', port: filesPort, path: '/files', protocol: 'http' });
  }

  renderPanelEditorRows();
  await refreshDiscoveredServices();
  alert(`Files service started on ${host}:${filesPort} and wired into this workspace.`);
}

async function launchFilesServiceFromPanelEditor() {
  const rawPort = prompt('Start fileserver on port (blank = auto pick):', '');
  if (rawPort === null) return;

  let requestedPort = null;
  if (rawPort.trim()) {
    const parsed = Number(rawPort.trim());
    if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
      alert('Invalid port. Use a number between 1 and 65535.');
      return;
    }
    requestedPort = parsed;
  }

  await startFilesServiceForEditor(requestedPort);
}

function savePanelEditor() {
  if (!state.panelEditor) return;
  const server = state.sessions.find((s) => s.name === state.panelEditor.name);
  if (!server) return;
  const newLabel = (document.getElementById('panel-session-label').value || '').trim();
  server.display = newLabel || server.name;
  server.color = state.panelEditor.color;
  server.tabs = normalizePanels(state.panelEditor.panels);
  server.panels = server.tabs;
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

  let targetHost;
  let nameSeed = 'workspace';

  if (isNew) {
    targetHost = addServerIp.value.trim();
    if (!targetHost) {
      alert('Please set the server IP/host.');
      return;
    }
    nameSeed = label;
  } else if (selected.startsWith('machine:')) {
    const machineName = selected.slice('machine:'.length);
    const knownMachine = HOMELAB_SERVERS.find((s) => s.name === machineName);
    if (!knownMachine) return;
    targetHost = knownMachine.ip;
    nameSeed = knownMachine.name;
  } else if (selected.startsWith('current:')) {
    const currentName = selected.slice('current:'.length);
    const selectedProfile = state.sessions.find((s) => s.name === currentName);
    if (!selectedProfile) return;
    targetHost = sessionMachineHost(selectedProfile) || selectedProfile.ip;
    nameSeed = selectedProfile.machine?.name || selectedProfile.name;
  } else {
    return;
  }

  const tabs = buildWorkspaceTabs();
  const newProfile = {
    name: makeUniqueName(`${nameSeed}-${label}-${session}`),
    display: label,
    machine: {
      name: isNew ? label : nameSeed,
      host: targetHost,
    },
    ip: targetHost,
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
    state.controlSessionsSignature = JSON.stringify(state.sessions);
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
    const dot = document.createElement('span');
    dot.className = 'tab-dot unknown';
    dot.title = 'Checking panel health...';
    const text = document.createElement('span');
    text.textContent = panel.label;
    btn.appendChild(dot);
    btn.appendChild(text);
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

    probePanelStatus(server, panel, endpoint).then((status) => {
      dot.classList.remove('unknown', 'up', 'down');
      dot.classList.add(status.up ? 'up' : 'down');
      if (status.up) {
        dot.title = `${panel.label} up on ${status.host}:${endpoint?.port || panel.port} (${status.latency}ms)`;
      } else {
        dot.title = `${panel.label} down on ${status.host}:${endpoint?.port || panel.port}${status.error ? ` (${status.error})` : ''}`;
      }
    });

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
  document.getElementById('panel-launch-files').onclick = launchFilesServiceFromPanelEditor;
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
    if (e.key === 'Escape' && document.getElementById('update-modal').classList.contains('open')) closeUpdateModal();
    if (e.key === 'Escape' && document.getElementById('sandbox-modal').classList.contains('open')) closeSandboxModal();
  });

  await reloadSessions(state.activeSession);
  window.setInterval(() => {
    refreshSessionsFromControlIfChanged();
  }, 4000);
}

init();
