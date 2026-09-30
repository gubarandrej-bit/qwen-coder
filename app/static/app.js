/* DocCheck — общий JS: диалоговое окно хода проверки, загрузка файлов */

// ---------- плавающее диалоговое окно прогресса ----------
let _pollTimer = null;

function openDock() { document.getElementById('progressDock').classList.remove('hidden'); }
function closeDock() {
  document.getElementById('progressDock').classList.add('hidden');
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}
function toggleDock() { document.getElementById('progressDock').classList.toggle('min'); }

function pollCheckStatus(checkId, onDone) {
  openDock();
  const logEl = document.getElementById('dockLog');
  const fill = document.getElementById('dockFill');
  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/api/check/${checkId}/status`, {credentials:'same-origin'});
      const j = await r.json();
      if (!j.ok) return;
      fill.style.width = (j.progress || 0) + '%';
      logEl.textContent = (j.log || []).map(l => `[${l.t}] ${l.msg}`).join('\n');
      logEl.scrollTop = logEl.scrollHeight;
      if (j.status === 'done' || j.status === 'error') {
        clearInterval(_pollTimer); _pollTimer = null;
        if (onDone) onDone(j);
      }
    } catch (e) { /* сеть моргнула — продолжим опрос */ }
  }, 1500);
}

// ---------- загрузка файлов ----------
async function uploadFiles(input) {
  const files = input.files;
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  const sys = document.getElementById('selSystem'); if (sys) fd.append('system', sys.value);
  const dt = document.getElementById('selDoctype'); if (dt) fd.append('doctype', dt.value);

  const st = document.getElementById('uploadStatus');
  st.textContent = 'Загрузка и разбор файлов…';
  try {
    const r = await fetch('/api/upload', {method:'POST', body:fd, credentials:'same-origin'});
    const j = await r.json();
    if (j.ok) {
      let msg = `Загружено файлов: ${j.saved.length}.`;
      if (j.notes && j.notes.length) msg += '\nВНИМАНИЕ (данные не выдуманы):\n- ' + j.notes.join('\n- ');
      st.textContent = msg;
      setTimeout(() => location.reload(), 2200);
    } else st.textContent = 'Ошибка: ' + j.error;
  } catch (e) { st.textContent = 'Ошибка загрузки: ' + e; }
}

// drag&drop зона
document.addEventListener('DOMContentLoaded', () => {
  const dz = document.getElementById('dropzone');
  if (dz) {
    ['dragover','dragenter'].forEach(ev => dz.addEventListener(ev, e => {
      e.preventDefault(); dz.classList.add('on');
    }));
    ['dragleave','drop'].forEach(ev => dz.addEventListener(ev, e => {
      e.preventDefault(); dz.classList.remove('on');
    }));
    dz.addEventListener('drop', e => {
      const inp = document.getElementById('fileInput');
      inp.files = e.dataTransfer.files;
      uploadFiles(inp);
    });
  }
});

async function apiPost(url, body) {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(body||{}), credentials:'same-origin'});
  return r.json();
}
async function apiDelete(url) {
  const r = await fetch(url, {method:'DELETE', credentials:'same-origin'});
  return r.json();
}
async function apiPut(url, body) {
  const r = await fetch(url, {method:'PUT', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(body||{}), credentials:'same-origin'});
  return r.json();
}
