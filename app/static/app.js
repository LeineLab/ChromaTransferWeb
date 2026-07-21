/**
 * Chroma Transfer – frontend application
 * Vanilla JS, ES2020+, no external framework dependencies.
 */

'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let machines = [];
let selectedMachineId = null;
let currentTransferId = null;
let currentEventSource = null;
let editingMachineId = null;
let selectedFile = null;

// Bootstrap modal instance
let machineModal = null;

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
  machineModal = new bootstrap.Modal(document.getElementById('machineModal'));

  await loadMachines();
  await loadUserInfo();
  setupDragDrop();
  setupFileInput();
  setupButtons();
});

// ---------------------------------------------------------------------------
// User Info
// ---------------------------------------------------------------------------

async function loadUserInfo() {
  try {
    const resp = await fetch('/auth/me');
    if (!resp.ok) return; // OIDC disabled or not logged in – hide user area
    const user = await resp.json();
    const userArea = document.getElementById('userArea');
    const userName = document.getElementById('userName');
    userArea.style.removeProperty('display');
    userArea.classList.remove('d-none');
    userName.textContent = user.name || user.email || user.sub || '';
  } catch (_) {
    // Ignore – OIDC not enabled
  }
}

// ---------------------------------------------------------------------------
// Machine CRUD
// ---------------------------------------------------------------------------

async function loadMachines() {
  try {
    const resp = await fetch('/api/machines/');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    machines = await resp.json();
    renderMachines(machines);
  } catch (err) {
    appendLog(`[Error] Failed to load machines: ${err.message}`);
  }
}

function renderMachines(list) {
  const ul = document.getElementById('machineList');
  const noMsg = document.getElementById('noMachinesMsg');
  const select = document.getElementById('machineSelect');

  // Clear existing items (keep noMachinesMsg)
  Array.from(ul.querySelectorAll('.machine-item')).forEach(el => el.remove());

  // Rebuild <select> options
  select.innerHTML = '<option value="">— select a machine —</option>';

  if (list.length === 0) {
    noMsg.classList.remove('d-none');
  } else {
    noMsg.classList.add('d-none');
    list.forEach(m => {
      // List item
      const li = document.createElement('li');
      li.className =
        'list-group-item list-group-item-action machine-item d-flex justify-content-between align-items-center';
      li.dataset.id = m.id;
      if (m.id === selectedMachineId) li.classList.add('selected');

      const info = document.createElement('span');
      info.className = 'machine-info';
      info.innerHTML = `<span class="fw-semibold">${escHtml(m.name)}</span>
        <span class="text-secondary small ms-2">${escHtml(m.ip)}</span>`;
      info.style.cursor = 'pointer';
      info.addEventListener('click', () => selectMachine(m.id));

      const btns = document.createElement('span');
      btns.className = 'd-flex gap-1';

      const editBtn = document.createElement('button');
      editBtn.className = 'btn btn-outline-secondary btn-xs';
      editBtn.title = 'Edit';
      editBtn.innerHTML =
        '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" fill="currentColor" class="bi bi-pencil" viewBox="0 0 16 16"><path d="M12.146.146a.5.5 0 0 1 .708 0l3 3a.5.5 0 0 1 0 .708l-10 10a.5.5 0 0 1-.168.11l-5 2a.5.5 0 0 1-.65-.65l2-5a.5.5 0 0 1 .11-.168zM11.207 2.5 13.5 4.793 14.793 3.5 12.5 1.207zm1.586 3L10.5 3.207 4 9.707V10h.5a.5.5 0 0 1 .5.5v.5h.5a.5.5 0 0 1 .5.5v.5h.293zm-9.761 5.175-.106.106-1.528 3.821 3.821-1.528.106-.106A.5.5 0 0 1 5 12.5V12h-.5a.5.5 0 0 1-.5-.5V11h-.5a.5.5 0 0 1-.468-.325"/></svg>';
      editBtn.addEventListener('click', e => { e.stopPropagation(); openEditModal(m); });

      const delBtn = document.createElement('button');
      delBtn.className = 'btn btn-outline-danger btn-xs';
      delBtn.title = 'Delete';
      delBtn.innerHTML =
        '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" fill="currentColor" class="bi bi-trash" viewBox="0 0 16 16"><path d="M5.5 5.5A.5.5 0 0 1 6 6v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5m2.5 0a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5m3 .5a.5.5 0 0 0-1 0v6a.5.5 0 0 0 1 0z"/><path d="M14.5 3a1 1 0 0 1-1 1H13v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V4h-.5a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1H6a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1h3.5a1 1 0 0 1 1 1zM4.118 4 4 4.059V13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.059L11.882 4zM2.5 3h11V2h-11z"/></svg>';
      delBtn.addEventListener('click', e => { e.stopPropagation(); deleteMachine(m.id); });

      btns.append(editBtn, delBtn);
      li.append(info, btns);
      ul.appendChild(li);

      // Select option
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = `${m.name} (${m.ip})`;
      if (m.id === selectedMachineId) opt.selected = true;
      select.appendChild(opt);
    });
  }
}

function selectMachine(id) {
  selectedMachineId = id;
  // Sync dropdown
  document.getElementById('machineSelect').value = id ?? '';
  // Highlight list item
  document.querySelectorAll('#machineList .machine-item').forEach(li => {
    li.classList.toggle('selected', Number(li.dataset.id) === id);
  });
}

function openAddModal() {
  editingMachineId = null;
  document.getElementById('machineModalLabel').textContent = 'Add Machine';
  document.getElementById('modalMachineName').value = '';
  document.getElementById('modalMachineIp').value = '';
  hideModalError();
  machineModal.show();
}

function openEditModal(machine) {
  editingMachineId = machine.id;
  document.getElementById('machineModalLabel').textContent = 'Edit Machine';
  document.getElementById('modalMachineName').value = machine.name;
  document.getElementById('modalMachineIp').value = machine.ip;
  hideModalError();
  machineModal.show();
}

async function saveMachine() {
  const name = document.getElementById('modalMachineName').value.trim();
  const ip = document.getElementById('modalMachineIp').value.trim();

  if (!name || !ip) {
    showModalError('Name and IP are required.');
    return;
  }

  const url = editingMachineId
    ? `/api/machines/${editingMachineId}`
    : '/api/machines/';
  const method = editingMachineId ? 'PUT' : 'POST';

  try {
    const resp = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, ip }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: 'Unknown error' }));
      const detail =
        typeof err.detail === 'string'
          ? err.detail
          : JSON.stringify(err.detail);
      showModalError(detail);
      return;
    }
    machineModal.hide();
    await loadMachines();
    // Re-select if we just edited the currently selected machine
    if (editingMachineId === selectedMachineId) {
      selectMachine(selectedMachineId);
    }
  } catch (err) {
    showModalError(err.message);
  }
}

async function deleteMachine(id) {
  if (!confirm('Delete this machine?')) return;
  try {
    const resp = await fetch(`/api/machines/${id}`, { method: 'DELETE' });
    if (!resp.ok && resp.status !== 204) {
      const err = await resp.json().catch(() => ({ detail: 'Unknown error' }));
      appendLog(`[Error] ${err.detail}`);
      return;
    }
    if (selectedMachineId === id) selectedMachineId = null;
    await loadMachines();
  } catch (err) {
    appendLog(`[Error] Delete failed: ${err.message}`);
  }
}

function showModalError(msg) {
  const el = document.getElementById('modalError');
  el.textContent = msg;
  el.classList.remove('d-none');
}

function hideModalError() {
  document.getElementById('modalError').classList.add('d-none');
}

// ---------------------------------------------------------------------------
// File handling
// ---------------------------------------------------------------------------

function setupDragDrop() {
  const area = document.getElementById('dropArea');

  area.addEventListener('dragover', e => {
    e.preventDefault();
    area.classList.add('drag-over');
  });

  area.addEventListener('dragleave', () => {
    area.classList.remove('drag-over');
  });

  area.addEventListener('drop', e => {
    e.preventDefault();
    area.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) setFile(file);
  });
}

function setupFileInput() {
  const input = document.getElementById('fileInput');
  input.addEventListener('change', () => {
    if (input.files[0]) setFile(input.files[0]);
  });
}

function setFile(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['dst', 'dsb'].includes(ext)) {
    appendLog(`[Error] Unsupported file type: .${ext}. Only .dst and .dsb are supported.`);
    return;
  }

  selectedFile = file;

  // Auto-fill short name: strip non-ASCII, control chars, and FAT-illegal
  // characters, then take the first 8 remaining chars.
  const base = file.name.replace(/\.[^.]+$/, '');
  const shortName = base
    .replace(/[^\x20-\x7e]/g, '')   // non-printable / non-ASCII
    .replace(/[/\\:*?"<>|.]/g, '')  // FAT-illegal
    .slice(0, 8);
  document.getElementById('shortNameInput').value = shortName;

  // Show filename badge
  document.querySelector('.upload-area-content').classList.add('d-none');
  const display = document.getElementById('fileDisplay');
  display.classList.remove('d-none');
  document.getElementById('fileNameText').textContent =
    `${file.name}  (${formatBytes(file.size)})`;
}

function clearFile() {
  selectedFile = null;
  document.getElementById('fileInput').value = '';
  document.querySelector('.upload-area-content').classList.remove('d-none');
  document.getElementById('fileDisplay').classList.add('d-none');
  document.getElementById('shortNameInput').value = '';
}

// ---------------------------------------------------------------------------
// Transfer
// ---------------------------------------------------------------------------

async function startTransfer() {
  if (!selectedFile) {
    appendLog('[Error] No file selected.');
    return;
  }

  // Resolve machine from dropdown (takes precedence) or sidebar selection
  const selectEl = document.getElementById('machineSelect');
  const machineId = parseInt(selectEl.value || selectedMachineId, 10);
  if (!machineId) {
    appendLog('[Error] No machine selected.');
    return;
  }

  const shortName = document.getElementById('shortNameInput').value.trim();

  clearLog();
  setProgress(0, 'Starting transfer…');
  setSending(true);

  const formData = new FormData();
  formData.append('machine_id', machineId);
  formData.append('file', selectedFile, selectedFile.name);
  if (shortName) formData.append('short_name', shortName);

  try {
    const resp = await fetch('/api/transfers/', {
      method: 'POST',
      body: formData,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: 'Unknown error' }));
      const detail =
        typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
      appendLog(`[Error] ${detail}`);
      setSending(false);
      return;
    }
    const { transfer_id: transferId } = await resp.json();
    currentTransferId = transferId;
    openEventSource(transferId);
  } catch (err) {
    appendLog(`[Error] ${err.message}`);
    setSending(false);
  }
}

function openEventSource(transferId) {
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }

  const es = new EventSource(`/api/transfers/${transferId}/events`);
  currentEventSource = es;

  es.addEventListener('progress', e => {
    try {
      const { sent, total } = JSON.parse(e.data);
      const pct = total > 0 ? Math.round((sent / total) * 100) : 0;
      setProgress(pct, `Sending chunk ${sent} of ${total}…`);
    } catch (_) {}
  });

  es.addEventListener('log', e => {
    try {
      const { message } = JSON.parse(e.data);
      appendLog(message);
    } catch (_) {
      appendLog(e.data);
    }
  });

  es.addEventListener('done', e => {
    try {
      const { message } = JSON.parse(e.data);
      appendLog(`✓ ${message}`);
    } catch (_) {}
    setProgress(100, 'Done');
    finishTransfer();
  });

  es.addEventListener('error', e => {
    try {
      const { message } = JSON.parse(e.data);
      appendLog(`[Error] ${message}`);
    } catch (_) {
      appendLog('[Error] Transfer failed.');
    }
    setProgress(0, 'Failed');
    finishTransfer();
  });

  // Native EventSource error (connection dropped)
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      // Already handled or connection closed normally
      finishTransfer();
    }
  };
}

function finishTransfer() {
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }
  currentTransferId = null;
  setSending(false);
}

async function cancelTransfer() {
  if (!currentTransferId) return;
  appendLog('Cancelling transfer…');
  try {
    await fetch(`/api/transfers/${currentTransferId}/cancel`, { method: 'POST' });
  } catch (_) {}
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function setSending(active) {
  const btnSend = document.getElementById('btnSend');
  const btnCancel = document.getElementById('btnCancel');
  const machineList = document.getElementById('machineList');
  const fileInput = document.getElementById('fileInput');
  const machineSelect = document.getElementById('machineSelect');

  if (active) {
    btnSend.classList.add('d-none');
    btnCancel.classList.remove('d-none');
    machineList.classList.add('pe-none', 'opacity-50');
    fileInput.disabled = true;
    machineSelect.disabled = true;
  } else {
    btnSend.classList.remove('d-none');
    btnCancel.classList.add('d-none');
    machineList.classList.remove('pe-none', 'opacity-50');
    fileInput.disabled = false;
    machineSelect.disabled = false;
  }
}

function setProgress(pct, label) {
  const bar = document.getElementById('progressBar');
  const lblEl = document.getElementById('progressLabel');
  const pctEl = document.getElementById('progressPct');
  bar.style.width = `${pct}%`;
  bar.setAttribute('aria-valuenow', pct);
  if (label !== undefined) lblEl.textContent = label;
  pctEl.textContent = `${pct}%`;

  if (pct >= 100) {
    bar.classList.remove('progress-bar-animated');
  } else {
    bar.classList.add('progress-bar-animated');
  }
}

function appendLog(msg) {
  const area = document.getElementById('logArea');
  const line = document.createElement('div');
  line.className = 'log-line';
  const ts = new Date().toLocaleTimeString();
  line.textContent = `[${ts}] ${msg}`;
  area.appendChild(line);
  area.scrollTop = area.scrollHeight;
}

function clearLog() {
  document.getElementById('logArea').innerHTML = '';
}

// ---------------------------------------------------------------------------
// Button wiring
// ---------------------------------------------------------------------------

function setupButtons() {
  document.getElementById('btnAddMachine').addEventListener('click', openAddModal);
  document.getElementById('btnModalSave').addEventListener('click', saveMachine);
  document.getElementById('btnSend').addEventListener('click', startTransfer);
  document.getElementById('btnCancel').addEventListener('click', cancelTransfer);
  document.getElementById('btnClearLog').addEventListener('click', clearLog);
  document.getElementById('btnClearFile').addEventListener('click', clearFile);

  // Sync machine selection from dropdown
  document.getElementById('machineSelect').addEventListener('change', e => {
    const val = parseInt(e.target.value, 10);
    if (!isNaN(val)) selectMachine(val);
    else selectMachine(null);
  });

  // Allow Enter key to save modal
  document.getElementById('machineModal').addEventListener('keydown', e => {
    if (e.key === 'Enter') saveMachine();
  });

  // Strip characters that are invalid in a DOS 8.3 filename field as the user
  // types.  Two rules, applied in order:
  //   1. Non-printable / non-ASCII  → removed  (0x20–0x7E is the valid range)
  //   2. FAT-illegal chars          → removed  (/ \ : * ? " < > | .)
  //      The dot is excluded because the machine reintroduces it as the
  //      name/extension separator when storing the file.
  const _FAT_ILLEGAL = /[/\\:*?"<>|.]/g;
  const shortNameInput = document.getElementById('shortNameInput');
  shortNameInput.addEventListener('input', () => {
    const cleaned = shortNameInput.value
      .replace(/[^\x20-\x7e]/g, '')
      .replace(_FAT_ILLEGAL, '');
    if (cleaned !== shortNameInput.value) {
      // Preserve cursor position when characters are removed mid-string.
      const pos = shortNameInput.selectionStart - (shortNameInput.value.length - cleaned.length);
      shortNameInput.value = cleaned;
      shortNameInput.setSelectionRange(pos, pos);
    }
  });
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

function escHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
