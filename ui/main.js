// Tavinor's notch (phase E1). Started by tavinor.py, which passes the bridge
// port and a UI-only token in the environment. All features live in Python;
// this process draws them and forwards clicks.
const { app, BrowserWindow, ipcMain, screen, desktopCapturer } = require('electron');
const http = require('http');
const path = require('path');

const PORT = Number(process.env.TAVINOR_PORT);
const TOKEN = process.env.TAVINOR_UI_TOKEN;
const W = 640, H = 900;               // head, pill, a card and up to 3 toasts

if (!PORT || !TOKEN || !app.requestSingleInstanceLock()) {
  console.error('Start Tavinor with tavinor.py; it launches this window.');
  app.exit(1);
}

function call(tool, args = {}, timeout = 30000) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ tool, args });
    const req = http.request({
      host: '127.0.0.1', port: PORT, path: '/', method: 'POST', timeout,
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body),
                 'X-Tavinor-UI': TOKEN },
    }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        try {
          const out = JSON.parse(data);
          out.ok ? resolve(out.data) : reject(new Error(out.error || 'failed'));
        } catch (e) { reject(e); }
      });
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', reject);
    req.end(body);
  });
}

let win;
let side = 'center', where = 'primary', onDisplay = null, isOpen = false;

// Settings: its own glass window beside the notch, movable by its header.
// Settings > Display: a chosen screen, the main one, or the pointer's.
function targetDisplay() {
  if (where === 'mouse') return screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
  return screen.getAllDisplays().find((d) => String(d.id) === where) || screen.getPrimaryDisplay();
}

function reportDisplays() {
  const p = screen.getPrimaryDisplay().id;
  const list = screen.getAllDisplays().map((d, i) => ({
    id: String(d.id),
    label: (d.label || 'Screen ' + (i + 1)) + (d.id === p ? ' (main)' : ''),
    size: d.size.width + ' × ' + d.size.height,
  }));
  call('displays', { list }).catch(() => {});
}

function place() {
  const dd = targetDisplay();
  onDisplay = dd.id;
  const d = dd.bounds;
  const x = side === 'left' ? d.x + 40
          : side === 'right' ? d.x + d.width - W - 40
          : d.x + Math.round((d.width - W) / 2);
  win.setBounds({ x, y: d.y, width: W, height: H });
}

// Events from Python (status, answers, face) by long-poll. Python gone for
// a few tries in a row means Tavinor quit: so do we.
async function pump(after) {
  let misses = 0;
  for (;;) {
    try {
      const evs = await call('events', { after, wait: 20 }, 25000);
      misses = 0;
      for (const e of evs) after = Math.max(after, e.id);
      const open = [win].filter((w) => w && !w.isDestroyed());
      // Every frame: Settings and Chat are a frame inside the notch.
      if (evs.length) for (const w of open) for (const f of w.webContents.mainFrame.framesInSubtree) f.send('events', evs);
      for (const e of evs) if (e.kind === 'place' && e.data) { where = e.data.screen; side = e.data.side; place(); }
    } catch (e) {
      if (++misses >= 4) return app.quit();
      await new Promise((r) => setTimeout(r, 750));
    }
  }
}

// The cursor, for the eyes: only while open, 30 times a second.
let lookTimer = null;
function looking(on) {
  clearInterval(lookTimer);
  lookTimer = null;
  if (!on) return;
  lookTimer = setInterval(() => {
    if (!win || win.isDestroyed()) return;
    const p = screen.getCursorScreenPoint();
    const b = win.getBounds();
    win.webContents.send('cursor', { x: p.x - b.x, y: p.y - b.y });
  }, 33);
}

// "Liquid glass": a transparent window cannot blur what is behind it, so the
// glass blurs a picture of the screen under the notch, taken as it opens.
// Kept in memory only.
async function backdrop(target = win) {
  const d = screen.getDisplayMatching(target.getBounds());
  const sf = d.scaleFactor;
  const src = await desktopCapturer.getSources({
    types: ['screen'],
    thumbnailSize: { width: Math.round(d.bounds.width * sf), height: Math.round(d.bounds.height * sf) },
  });
  const s = src.find((x) => String(x.display_id) === String(d.id)) || src[0];
  if (!s) return null;
  const b = target.getBounds();
  const crop = s.thumbnail.crop({
    x: Math.round((b.x - d.bounds.x) * sf), y: Math.round((b.y - d.bounds.y) * sf),
    width: Math.round(b.width * sf), height: Math.round(b.height * sf),
  });
  return crop.toDataURL();
}

app.whenReady().then(async () => {
  let hello = { last: 0, face: 'idle', name: 'Tavinor', side: 'center' };
  try { hello = await call('hello'); } catch (e) { /* Python still starting */ }
  side = hello.side || 'center';
  where = hello.screen || 'primary';

  win = new BrowserWindow({
    width: W, height: H, frame: false, transparent: true, resizable: false,
    movable: false, alwaysOnTop: true, skipTaskbar: true, hasShadow: false,
    show: false, backgroundColor: '#00000000',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true,
                      nodeIntegration: false, sandbox: true, nodeIntegrationInSubFrames: true },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setIgnoreMouseEvents(true, { forward: true });
  place();
  reportDisplays();
  screen.on('display-metrics-changed', place);
  for (const ev of ['display-added', 'display-removed']) screen.on(ev, () => { reportDisplays(); place(); });
  // Follow the mouse: hop screens only while closed, so it never jumps mid-use.
  setInterval(() => {
    if (where !== 'mouse' || isOpen || !win || win.isDestroyed()) return;
    if (screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).id !== onDisplay) place();
  }, 400);
  win.loadFile('index.html');
  win.once('ready-to-show', () => {
    win.showInactive();
    win.webContents.send('hello', hello);
  });

  ipcMain.handle('call', (_e, tool, args) => call(tool, args, tool === 'day' ? 15000 : 30000));
  ipcMain.handle('backdrop', () => backdrop().catch(() => null));
  ipcMain.on('interactive', (_e, on) => win.setIgnoreMouseEvents(!on, { forward: true }));
  ipcMain.on('open', (_e, on) => {
    isOpen = on;
    looking(on);
    if (!on) win.blur();
  });
  ipcMain.on('focus', () => win.focus());
  ipcMain.on('close-me', (e) => BrowserWindow.fromWebContents(e.sender)?.close());

  pump(hello.last || 0);
});

app.on('window-all-closed', () => app.quit());
