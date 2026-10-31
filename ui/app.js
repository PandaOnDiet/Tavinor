// Tavinor notch: hover to open, the face, the pill, the Today card.
'use strict';
const $ = (id) => document.getElementById(id);
const w = $('w'), face = $('face'), eyes = $('eyes'), head = $('head');
const q = $('q'), wave = $('wave'), mic = $('btn-mic');
const today = $('today'), reply = $('reply');
const pane = $('pane'), paneFrame = $('pane-frame');

const GLOW = {
  idle: 'rgba(110,150,255,.18)', listening: 'rgba(76,141,255,.6)', thinking: 'rgba(160,120,255,.55)',
  done: 'rgba(70,210,140,.5)', error: 'rgba(255,90,90,.55)', focus: 'rgba(60,210,200,.45)',
  eyes: 'rgba(255,138,61,.6)', sleeping: 'rgba(0,0,0,0)', dizzy: 'rgba(255,205,90,.5)',
};
const HEAD_X = 320, HEAD_Y = 78;       // face centre in window pixels
const SLEEP_AFTER = 10 * 60 * 1000;

let open = false, mode = 'idle', asked = '', interactive = false;
let closeTimer = null, dwellTimer = null, lastActive = Date.now();
let shot = null, shotAt = 0;
let config = { hover: true, hover_slow: false };   // Settings > Hover

// ---------------------------------------------------------------- face
// The logo's eyes (specs/brand/faces): 5 rows, left eye then right eye,
// 3 cells each. # on, . off, g on as the red glint shifted one cell right.
const PIX = {
  rest:    ['######', '###ggg', '######', '######', '######'],
  full:    ['######', '######', '######', '######', '######'],
  focused: ['......', '######', '######', '######', '######'],
  happy:   ['......', '.#..#.', '#.##.#', '#.##.#', '......'],
  sleepy:  ['......', '......', '......', '######', '######'],
  dizzy:   ['......', '#.##.#', '.#..#.', '#.##.#', '......'],   // X_X
};
const FACE_PIX = { idle: 'rest', listening: 'rest', thinking: 'full', done: 'happy', error: 'full',
                   focus: 'focused', eyes: 'full', sleeping: 'sleepy', dizzy: 'dizzy' };
for (const e of eyes.children) for (let i = 0; i < 15; i++) e.append(document.createElement('i'));

function paintEyes(rows) {
  [...eyes.children].forEach((e, side) => [...e.children].forEach((c, i) => {
    const ch = rows[Math.floor(i / 3)][side * 3 + (i % 3)];
    c.className = ch === '.' ? '' : ch === 'g' ? 'on g' : 'on';
  }));
}

function setFace(state) {
  if (!GLOW[state]) state = 'idle';
  mode = state;
  face.className = 'face s-' + state;
  paintEyes(PIX[FACE_PIX[state]]);
  head.style.boxShadow = 'inset 0 1px 0 rgba(255,255,255,.28), 0 24px 64px rgba(0,0,0,.5), 0 0 80px ' + GLOW[state];
  const listening = state === 'listening';
  wave.hidden = !listening;
  q.hidden = listening;
  mic.classList.toggle('on', listening);
  if (state !== 'idle' && state !== 'sleeping') lastActive = Date.now();
}

// Spun in circles (main.js watches the pointer): X_X eyes, a wobble and
// orbiting stars for a few seconds, then back to whatever it was doing.
// Closed, the head drops into view for it.
let dizzyTimer = null;
window.tav.on('dizzy', () => {
  if (mode === 'dizzy' || mode === 'listening' || mode === 'thinking') return;
  const back = mode;
  setFace('dizzy');
  w.classList.add('peek');
  clearTimeout(dizzyTimer);
  dizzyTimer = setTimeout(() => {
    if (mode === 'dizzy') setFace(back);
    layoutToasts();                   // puts `peek` back the way toasts want it
  }, 3200);
});

window.tav.on('cursor', (p) => {
  if (mode === 'thinking') { eyes.style.transform = ''; return; }
  const cl = (v, a) => Math.max(-a, Math.min(a, v));
  eyes.style.transform = `translate(${cl((p.x - HEAD_X) / 38, 9)}px, ${cl((p.y - HEAD_Y) / 38, 5)}px)`;
});

setInterval(() => {
  if (!open && mode === 'idle' && Date.now() - lastActive > SLEEP_AFTER) setFace('sleeping');
}, 30000);

// ---------------------------------------------------------------- glass
// A picture of the screen behind the notch, blurred under each glass piece.
async function grab() {
  if (shot && Date.now() - shotAt < 1500) return shot;
  shot = await window.tav.backdrop();
  shotAt = Date.now();
  if (shot) {
    for (const bd of document.querySelectorAll('.bd')) paint(bd);
    document.body.classList.add('has-bd');
    document.body.style.setProperty('--tint', String(tintFor(await brightness(shot))));
  }
  return shot;
}

// Average brightness (0 dark .. 1 white) of the screen behind the pill and
// cards, from a small copy of the picture.
async function brightness(src) {
  const img = new Image();
  img.src = src;
  try { await img.decode(); } catch { return 0; }
  const c = document.createElement('canvas');
  c.width = 32; c.height = 45;                       // the 640 x 900 window, 20x smaller
  const g = c.getContext('2d', { willReadFrequently: true });
  g.drawImage(img, 0, 0, c.width, c.height);
  const px = g.getImageData(0, 8, c.width, 23).data; // rows behind the pill and cards
  let sum = 0;
  for (let i = 0; i < px.length; i += 4) sum += (0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]) / 255;
  return sum / (px.length / 4);
}

// Dark screens get no extra layer; a white one gets .72, enough for white text.
const tintFor = (lum) => Math.min(0.72, Math.max(0, (lum - 0.3) * 1.1));

// Line one glass piece's blurred picture up with the screen behind it.
function paint(bd) {
  if (!shot) return;
  const el = bd.parentElement;
  let x = 20 + el.offsetLeft, y = el.offsetTop;
  if (el.classList.contains('toast')) { x += $('toasts').offsetLeft - 20; y += $('toasts').offsetTop; }
  const i = bd.firstElementChild;
  i.style.left = -x + 'px';
  i.style.top = -y + 'px';
  i.style.backgroundImage = `url("${shot}")`;
}

// ---------------------------------------------------------------- open/close
function pinned() {
  return document.activeElement === q || mode === 'listening' || mode === 'thinking' || !pane.hidden;
}

async function openNow(focusInput) {
  clearTimeout(closeTimer);
  if (!open) {
    // The picture must be taken before the head drops in, or it shows itself.
    await Promise.race([grab(), new Promise((r) => setTimeout(r, 300))]);
    open = true;
    if (mode === 'sleeping') setFace('idle');
    lastActive = Date.now();
    w.classList.add('is-open');
    window.tav.opened(true);
    showPill();
  }
  layoutToasts();
  setInteractive(true);
  if (focusInput) { window.tav.focus(); q.focus(); }
}

function close() {
  clearTimeout(closeTimer);
  if (!open) return;
  open = false;
  w.classList.remove('is-open');
  window.tav.opened(false);
  q.blur();
  setInteractive(false);
  setTimeout(() => { if (!open) showPill(); }, 400);
  layoutToasts();
}

// ---------------------------------------------------------------- toasts
const toastBox = $('toasts');
const peeking = () => toastBox.children.length > 0;

function layoutToasts() {
  // Closed: right under the head, which peeks down while any toast is up.
  // Open: under whichever card shows.
  let top = 168;
  if (open) {
    const card = !pane.hidden ? pane : !menu.hidden ? menu : !reply.hidden ? reply : !today.hidden ? today : $('pill');
    top = card.offsetTop + card.offsetHeight + 12;
  }
  toastBox.style.top = top + 'px';
  w.classList.toggle('peek', peeking() && !open);
}

async function showToast(t) {
  if (!open) await Promise.race([grab(), new Promise((r) => setTimeout(r, 300))]);
  const box = el('div', 'toast glass k-' + (t.kind || 'info'));
  box.dataset.id = t.id;
  const bd = el('div', 'bd'); bd.append(el('i'));
  box.append(bd, el('div', 'eyebrow', t.title));
  if (t.text) box.append(el('div', 't', t.text));
  if (t.sub) box.append(el('div', 's', t.sub));
  if (t.rows && t.rows.length) {
    const rows = el('div', 'rows');
    for (const [a, b] of t.rows) { const r = el('div', 'r'); r.append(el('span', '', a), el('span', '', b || '')); rows.append(r); }
    box.append(rows);
  }
  const acts = el('div', 'acts');
  for (const b of t.buttons) {
    const btn = el('button', 'btn ' + (b.primary ? 'white' : 'ghost'), b.label);
    btn.type = 'button';
    btn.addEventListener('click', () => {
      window.tav.call('toast_click', { id: t.id, button: b.id }).catch(() => {});
      dropToast(box);
    });
    acts.append(btn);
  }
  box.append(acts);
  toastBox.prepend(box);
  while (toastBox.children.length > 3) toastBox.lastElementChild.remove();
  layoutToasts();
  for (const x of toastBox.querySelectorAll('.bd')) paint(x);
}

function dropToast(box) {
  box.classList.add('out');
  setTimeout(() => { box.remove(); layoutToasts(); if (!peeking() && !open) setInteractive(false); }, 280);
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function closeSoon(ms) {
  clearTimeout(closeTimer);
  closeTimer = setTimeout(() => { if (!pinned()) close(); else closeSoon(1500); }, ms);
}

function setInteractive(on) {
  if (on !== interactive) { interactive = on; window.tav.interactive(on); }
}

// The window ignores the mouse except over the notch itself; Electron still
// forwards moves, so this sees the cursor everywhere over the window.
document.addEventListener('mousemove', (e) => {
  const over = e.target.closest && e.target.closest(
    open ? '.head, .pill, .card, .toast' : peeking() ? '.zone, .lip, .head, .toast' : '.zone, .lip');
  const onToast = over && over.classList.contains('toast');
  setInteractive(!!over);
  if (!open) {
    if (over && !onToast && !dwellTimer && config.hover) {
      grab();                                  // start the picture early
      dwellTimer = setTimeout(() => { dwellTimer = null; openNow(false); }, config.hover_slow ? 420 : 160);
    } else if (!over && dwellTimer) {
      clearTimeout(dwellTimer); dwellTimer = null;
    }
    return;
  }
  if (over) clearTimeout(closeTimer);
  else closeSoon(reply.hidden ? 500 : 2500);
});
document.addEventListener('mouseleave', () => { if (open) closeSoon(reply.hidden ? 500 : 2500); });

// ---------------------------------------------------------------- Today
const fmtDue = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  return d.toLocaleDateString('en-GB', { weekday: 'short' }) + ' ' +
         d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
};

function check() {
  const ns = 'http://www.w3.org/2000/svg';
  const s = document.createElementNS(ns, 'svg');
  s.setAttribute('width', '12'); s.setAttribute('height', '12'); s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('fill', 'none'); s.setAttribute('stroke', '#fff'); s.setAttribute('stroke-width', '3.2');
  s.setAttribute('stroke-linecap', 'round'); s.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', 'M5 12.5l4.5 4.5L19 7.5');
  s.appendChild(p);
  return s;
}

function renderDay(d) {
  const now = new Date();
  $('today-date').textContent = 'Today · ' + now.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
  $('today-count').textContent = d.open ? d.open + ' open' : '';
  $('next').hidden = !d.next;
  if (d.next) {
    $('next-title').textContent = d.next.title;
    $('next-when').textContent = d.next.start + ' – ' + d.next.end;
  }
  const list = $('tasks');
  list.replaceChildren();
  for (const t of d.tasks) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'task' + (t.sub ? ' sub' : '');
    const box = document.createElement('span'); box.className = 'box'; box.appendChild(check());
    const tt = document.createElement('span'); tt.className = 'tt'; tt.textContent = t.text;
    const due = document.createElement('span'); due.className = 'due'; due.textContent = fmtDue(t.due);
    b.append(box, tt, due);
    b.addEventListener('click', async () => {
      b.classList.add('done');
      try {
        await window.tav.call('done', { key: t.key });
        setTimeout(() => { b.classList.add('gone'); setTimeout(refreshDay, 350); }, 600);
      } catch (e) { b.classList.remove('done'); }
    });
    list.appendChild(b);
  }
  $('empty').hidden = d.tasks.length > 0;
  $('habit').hidden = !d.habit;
  if (d.habit) $('habit').textContent = d.habit + ' · not yet today';
  $('timer').textContent = d.focus ? d.focus.left + ' min left' : '';
  $('timer').hidden = !d.focus;
}

async function refreshDay() {
  try { renderDay(await window.tav.call('day')); } catch (e) { /* keep what is shown */ }
}

// Just the pill: what opening shows. Today is in the grid menu, so the
// notch takes no more of the screen than you ask for (2026-10-31).
function showPill() {
  reply.hidden = true;
  menu.hidden = true;
  pane.hidden = true;
  today.hidden = true;
  gridIcon(false);
  layoutToasts();
}

function showToday() {
  reply.hidden = true;
  menu.hidden = true;
  pane.hidden = true;
  gridIcon(false);
  today.hidden = false;
  refreshDay().then(layoutToasts);
  layoutToasts();
}

$('btn-speak').addEventListener('click', () => window.tav.call('speak', { text: $('reply-a').textContent }).catch(() => {}));

function showReply(question, text) {
  $('reply-q').textContent = question ? 'You · “' + question + '”' : '';
  $('reply-a').textContent = text || '…';
  today.hidden = true;
  menu.hidden = true;
  pane.hidden = true;
  gridIcon(false);
  reply.hidden = false;
  layoutToasts();
}

// ---------------------------------------------------------------- asking
function ask(text) {
  text = text.trim();
  if (!text) return;
  asked = text;
  showReply(text, '');
  window.tav.call('ask', { text }).catch((e) => showReply(text, 'Could not reach Tavinor: ' + e.message));
}

q.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { const t = q.value; q.value = ''; ask(t); }
  if (e.key === 'Escape') close();
});
q.addEventListener('blur', () => { if (open) closeSoon(2500); });
mic.addEventListener('click', () => { window.tav.call('mic').catch(() => {}); });
$('btn-focus').addEventListener('click', () => ask('start focus for 25 minutes'));
$('btn-plan').addEventListener('click', () => ask('plan my day'));
$('btn-back').addEventListener('click', showPill);
// ---------------------------------------------------------------- menu
// The grid button: a quiet two-column list. Each item is a phrase Tavinor
// already understands, sent the same way as typing it.
const menu = $('menu');
const MENU = [
  ['Chat', 'M5 6.5A2.5 2.5 0 017.5 4h9A2.5 2.5 0 0119 6.5v6a2.5 2.5 0 01-2.5 2.5H11l-4 3.5V15a2.5 2.5 0 01-2-2.5z', () => showPane('chat.html')],
  ['Today', 'M4.5 11L12 4.5l7.5 6.5M6.5 9.5V19.5h11V9.5', () => showToday()],
  ['Tasks', 'M5 5h14v14H5zM8.5 12l2.5 2.5 4.5-5', () => ask('what is left')],
  ['Notes', 'M7 4h7l4 4v12H7zM14 4v4h4M10 12h5M10 16h5', () => ask('notes')],
  ['Habits', 'M12 21c-3.5 0-6-2.5-6-6 0-3 2-5 3.5-7 .5 2 1.5 3 2.5 3 0-3 1.5-5.5 4-8 .5 3 2 4.5 2 8 0 5.5-2.5 10-6 10z', () => ask('habits')],
  ['Plan my day', 'M5 6.5h14v13H5zM5 10.5h14M9 4v4M15 4v4', () => ask('plan my day')],
  ['Tomorrow', 'M5 6.5h14v13H5zM5 10.5h14M9 4v4M15 4v4M10 15h4', () => ask('calendar tomorrow')],
  ['Focus 25 min', 'M12 21a7.5 7.5 0 100-15 7.5 7.5 0 000 15zM12 9.5v4l2.5 1.5M10 3h4', () => ask('start focus for 25 minutes')],
  ['Blocked sites', 'M12 20a8 8 0 100-16 8 8 0 000 16zM6.5 6.5l11 11', () => ask('blocked sites')],
  ['Settings', 'M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z', () => showSettings()],
];
for (const [label, d, go] of MENU) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'mi';
  b.innerHTML = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
  b.append(label);
  b.addEventListener('click', go);
  $('menu-list').append(b);
}

function gridIcon(on) {
  $('btn-settings').classList.toggle('x-on', on);
  $('btn-settings').setAttribute('aria-expanded', String(on));
}

// Settings and Chat open in one card under the pill, never a separate
// window. Each load starts fresh: Settings on its first page, Chat with
// its history (and the first message, when there is one).
async function showPane(url) {
  if (!open) await openNow(false);    // let opening finish first
  today.hidden = true;
  reply.hidden = true;
  menu.hidden = true;
  paneFrame.src = url;
  pane.hidden = false;
  gridIcon(true);
  layoutToasts();
  for (const x of pane.querySelectorAll('.bd')) paint(x);
  window.tav.focus();                 // so its text boxes take typing
}
const showSettings = () => showPane('settings.html');
const chatOpen = () => !pane.hidden && paneFrame.src.includes('chat.html');

// The pane's close button and Escape (from inside the frame) come back here.
window.addEventListener('message', (e) => {
  if (e.source !== paneFrame.contentWindow || !e.data) return;
  if (e.data.tav === 'close-pane') showPill();
  else if (e.data.tav === 'hover') { setInteractive(true); clearTimeout(closeTimer); }
});

$('btn-settings').addEventListener('click', () => {
  if (!menu.hidden || !pane.hidden) return showPill();
  today.hidden = true;
  reply.hidden = true;
  menu.hidden = false;
  gridIcon(true);
  layoutToasts();
  for (const x of menu.querySelectorAll('.bd')) paint(x);
});

// ---------------------------------------------------------------- from Python
// You clicked another window: close, even with Settings or Chat open (they
// pin it against the mouse leaving, not against you going elsewhere).
window.tav.on('blurred', () => { if (open) close(); });

window.tav.on('hello', (h) => {
  setFace(h.face || 'idle');
  if (h.config) config = h.config;
  q.placeholder = 'Ask ' + (h.name || 'Tavinor') + ', or tell it what to do';
});

window.tav.on('events', (evs) => {
  for (const e of evs) {
    const d = e.data || {};
    if (e.kind === 'face') setFace(d === 'eyes' ? 'eyes' : String(d));
    else if (e.kind === 'status') { showReply(asked, d.text); if (!open) openNow(false); }
    else if (e.kind === 'answer') { asked = d.question || asked; showReply(asked, d.text); if (!open) openNow(false); }
    else if (e.kind === 'heard') { asked = d.text; showReply(d.text, ''); }
    else if (e.kind === 'open') openNow(true);
    else if (e.kind === 'settings_open') showSettings();
    // An open chat sends `first` itself (it got this event too).
    else if (e.kind === 'chat_open' && !chatOpen()) showPane('chat.html?first=' + encodeURIComponent(d.first || ''));
    else if (e.kind === 'hide') close();
    else if (e.kind === 'config') config = d;
    else if (e.kind === 'toast') showToast(d);
    else if (e.kind === 'toast_gone') { const b = toastBox.querySelector(`[data-id="${d.id}"]`); if (b) dropToast(b); }
  }
});
