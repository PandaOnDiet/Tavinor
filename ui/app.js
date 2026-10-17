// Tavinor notch: hover to open, the face, the pill, the Today card.
'use strict';
const $ = (id) => document.getElementById(id);
const w = $('w'), face = $('face'), eyes = $('eyes'), head = $('head');
const q = $('q'), wave = $('wave'), mic = $('btn-mic');
const today = $('today'), reply = $('reply');

const GLOW = {
  idle: 'rgba(110,150,255,.18)', listening: 'rgba(76,141,255,.6)', thinking: 'rgba(160,120,255,.55)',
  done: 'rgba(70,210,140,.5)', error: 'rgba(255,90,90,.55)', focus: 'rgba(60,210,200,.45)',
  eyes: 'rgba(255,138,61,.6)', sleeping: 'rgba(0,0,0,0)',
};
const HEAD_X = 320, HEAD_Y = 78;       // face centre in window pixels
const SLEEP_AFTER = 10 * 60 * 1000;

let open = false, mode = 'idle', asked = '', interactive = false;
let closeTimer = null, dwellTimer = null, lastActive = Date.now();
let shot = null, shotAt = 0;
let config = { hover: true, hover_slow: false };   // Settings > Hover

// ---------------------------------------------------------------- face
function setFace(state) {
  if (!GLOW[state]) state = 'idle';
  mode = state;
  face.className = 'face s-' + state;
  head.style.boxShadow = 'inset 0 1px 0 rgba(255,255,255,.28), 0 24px 64px rgba(0,0,0,.5), 0 0 80px ' + GLOW[state];
  const listening = state === 'listening';
  wave.hidden = !listening;
  q.hidden = listening;
  mic.classList.toggle('on', listening);
  if (state !== 'idle' && state !== 'sleeping') lastActive = Date.now();
}

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
  }
  return shot;
}

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
  return document.activeElement === q || mode === 'listening' || mode === 'thinking';
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
    showToday();
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
  setTimeout(() => { if (!open) { reply.hidden = true; today.hidden = false; } }, 400);
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
    const card = reply.hidden ? today : reply;
    top = card.offsetTop + card.offsetHeight + 12;
  }
  toastBox.style.top = top + 'px';
  w.classList.toggle('peek', peeking() && !open);
}

async function showToast(t) {
  if (!open) await Promise.race([grab(), new Promise((r) => setTimeout(r, 300))]);
  const box = el('div', 'toast glass k-' + (t.kind || 'info'));
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
      box.classList.add('out');
      setTimeout(() => { box.remove(); layoutToasts(); if (!peeking() && !open) setInteractive(false); }, 280);
    });
    acts.append(btn);
  }
  box.append(acts);
  toastBox.prepend(box);
  while (toastBox.children.length > 3) toastBox.lastElementChild.remove();
  layoutToasts();
  for (const x of toastBox.querySelectorAll('.bd')) paint(x);
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

function showToday() {
  reply.hidden = true;
  today.hidden = false;
  refreshDay().then(layoutToasts);
  layoutToasts();
}

function showReply(question, text) {
  $('reply-q').textContent = question ? 'You · “' + question + '”' : '';
  $('reply-a').textContent = text || '…';
  today.hidden = true;
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
$('btn-back').addEventListener('click', showToday);
$('btn-settings').addEventListener('click', () => { window.tav.openSettings(); close(); });

// ---------------------------------------------------------------- from Python
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
    else if (e.kind === 'hide') close();
    else if (e.kind === 'config') config = d;
    else if (e.kind === 'toast') showToast(d);
  }
});
