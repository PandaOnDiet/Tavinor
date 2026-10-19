// Chat window: everything runs in Python's converse(); this only draws it.
'use strict';
const $ = (id) => document.getElementById(id);
const log = $('log'), input = $('input'), sendBtn = $('send');
let busy = false;
const SPK = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4zM16 9a4 4 0 010 6M18.5 6.5a7.5 7.5 0 010 11"/></svg>';

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// `code` in a message, as the old window did.
function withCode(text) {
  const f = document.createDocumentFragment();
  text.split(/`([^`]+)`/).forEach((p, i) => { if (p) f.append(i % 2 ? el('code', '', p) : p); });
  return f;
}

function atBottom() { return log.scrollHeight - log.scrollTop - log.clientHeight < 60; }
function show(node) {
  const stick = atBottom();
  log.querySelector('.empty')?.remove();
  log.append(node);
  if (stick || node.classList.contains('me')) log.scrollTop = log.scrollHeight;
}

function add(kind, text) {
  const m = el('div', 'm ' + kind);
  if (kind === 'me' || kind === 'say') m.append(withCode(text));
  if (kind === 'say') {
    const b = el('button', 'spk');
    b.type = 'button';
    b.title = 'Read aloud';
    b.setAttribute('aria-label', 'Read aloud');
    b.innerHTML = SPK;
    b.addEventListener('click', () => window.tav.call('speak', { text }).catch(() => {}));
    m.append(b);
  }
  else m.textContent = kind === 'result' ? text.replace(' -> ', '  →  ') : text;
  show(m);
}

function card(c) {
  if (log.querySelector(`[data-id="${c.id}"]`)) return;
  const box = el('div', 'm card');
  box.dataset.id = c.id;
  box.append(el('div', 'h', c.lines[0]));
  for (const l of c.lines.slice(1)) box.append(el('div', 'l', l));
  const acts = el('div', 'acts');
  c.buttons.forEach((label, i) => {
    const b = el('button', 'btn ' + (i === c.buttons.length - 1 ? 'primary' : ''), label);
    b.type = 'button';
    b.addEventListener('click', () => {
      acts.querySelectorAll('button').forEach((x) => { x.disabled = true; });
      window.tav.call('chat_decide', { id: c.id, button: i }).catch(() => {});
    });
    acts.append(b);
  });
  box.append(acts);
  show(box);
}

function settle(id, text) {
  const box = log.querySelector(`[data-id="${id}"]`);
  if (!box) return;
  box.querySelector('.acts')?.replaceWith(el('div', 'done', text));
}

function setBusy(on) {
  busy = on;
  $('typing').hidden = !on;
  sendBtn.disabled = on;
  if (on && atBottom()) log.scrollTop = log.scrollHeight;
}

function fit() {
  input.style.height = 'auto';
  input.style.height = input.scrollHeight + 'px';
}

async function send(text) {
  text = (text ?? input.value).trim();
  if (!text || busy) return;
  input.value = '';
  fit();
  setBusy(true);
  try {
    const r = await window.tav.call('chat_send', { text });
    if (r.busy) { add('note', 'Still working on the last message.'); }
  } catch (e) {
    add('error', "Couldn't send: " + e.message);
    setBusy(false);
  }
}

// ---------------------------------------------------------------- wiring
sendBtn.addEventListener('click', () => send());
input.addEventListener('input', fit);
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
// In the notch's pane (a frame) the card is the panel: no second border.
if (window.parent !== window) document.documentElement.classList.add('embed');

$('close').addEventListener('click', () => window.tav.closeMe());
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') window.tav.closeMe(); });

// Clear asks once: the first click arms it for three seconds.
let armed = null;
$('clear').addEventListener('click', async () => {
  const b = $('clear');
  if (!armed) {
    b.classList.add('armed');
    b.title = 'Click again to clear';
    armed = setTimeout(() => { armed = null; b.classList.remove('armed'); b.title = 'Clear the chat'; }, 3000);
    return;
  }
  clearTimeout(armed); armed = null; b.classList.remove('armed');
  await window.tav.call('chat_clear').catch(() => {});
  log.replaceChildren(empty());
});

$('claude').addEventListener('click', () => window.tav.call('chat_handoff').catch(() => {}));

window.tav.on('backdrop', (pic) => {
  if (!pic) return;
  $('bd').style.backgroundImage = `url("${pic}")`;
  $('panel').classList.add('has-bd');
});

window.tav.on('events', (evs) => {
  for (const e of evs) {
    const d = e.data || {};
    if (e.kind === 'chat_open' && d.first) send(d.first);
    if (e.kind !== 'chat') continue;
    if (d.kind === 'card') card(d);
    else if (d.kind === 'settle') settle(d.id, d.text);
    else if (d.kind === 'busy') { setBusy(d.on); if (d.model) $('model').textContent = d.model; }
    else add(d.kind, d.text || '');
  }
});

function empty() {
  return el('div', 'empty', 'Ask anything, or tell it what to do: set a timer, tidy Downloads, remind you at 5.');
}

(async () => {
  let h;
  try { h = await window.tav.call('chat_history'); } catch (e) {
    add('error', 'Could not load the chat: ' + e.message);
    return;
  }
  $('title').textContent = h.name;
  $('model').textContent = h.model;
  $('claude').hidden = !h.claude;
  if (!h.rows.length && !h.cards.length) log.append(empty());
  for (const r of h.rows) add(r.kind, r.text);
  for (const c of h.cards) card(c);
  setBusy(h.busy);
  log.scrollTop = log.scrollHeight;
  input.focus();
  const first = new URLSearchParams(location.search).get('first');
  if (first) send(first);
})();
