// Settings window: draws whatever page uisettings.py describes.
'use strict';
const $ = (id) => document.getElementById(id);
const body = $('body');
let current = 'main';
const notes = {};                    // row id -> its note element, for late "note" events

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

async function set(id, value) {
  try {
    const r = await window.tav.call('settings_set', { page: current, id, value });
    if (r && r.note !== undefined && notes[id]) note(id, r.note, r.ok !== false);
    if (r && r.show !== undefined) sheet(id === 'log' ? 'Action log' : 'Details', r.show);
    if (r && r.reload) load(current, null, true);
    return r || {};
  } catch (e) {
    if (notes[id]) note(id, e.message, false);
    return { error: e.message };
  }
}

function note(id, text, ok) {
  const n = notes[id];
  if (!n) return;
  n.textContent = text || '';
  n.className = 'note' + (text ? (ok ? ' ok' : ' bad') : '');
}

function sheet(title, text) {
  $('sheet-title').textContent = title;
  $('sheet-text').textContent = text;
  $('sheet').hidden = false;
}

function txt(label, desc) {
  const t = el('div', 'txt');
  t.append(el('div', 'lb', label));
  if (desc) t.append(el('div', 'ds', desc));
  return t;
}

// ---------------------------------------------------------------- row types
const ROWS = {
  toggle(it) {
    const r = el('div', 'row');
    const sw = el('button', 'sw');
    sw.type = 'button';
    sw.setAttribute('role', 'switch');
    sw.setAttribute('aria-checked', String(it.value));
    sw.setAttribute('aria-label', it.label);
    sw.addEventListener('click', async () => {
      const on = sw.getAttribute('aria-checked') !== 'true';
      sw.setAttribute('aria-checked', String(on));
      const res = await set(it.id, on);
      if (res.error) sw.setAttribute('aria-checked', String(!on));
    });
    r.append(txt(it.label, it.desc), sw);
    return r;
  },

  link(it) {
    const r = el('button', 'row');
    r.type = 'button';
    r.append(txt(it.label, it.desc), el('span', 'chev', '›'));
    r.addEventListener('click', () => (it.action ? set(it.id, true) : load(it.id, 'in')));
    return r;
  },

  info(it) {
    const r = el('div', 'col');
    r.append(el('div', 'info' + (it.dim ? '' : ' bright'), it.text));
    return r;
  },

  radio(it) {
    const box = el('div', '');
    const rows = it.options.map((o) => {
      const r = el('button', 'row radio');
      r.type = 'button';
      r.setAttribute('role', 'radio');
      r.setAttribute('aria-checked', String(o.value === it.value));
      r.append(el('span', 'dot'), txt(o.label, o.desc));
      r.addEventListener('click', async () => {
        rows.forEach((x) => x.setAttribute('aria-checked', String(x === r)));
        await set(it.id, o.value);
      });
      return r;
    });
    box.append(...rows);
    return box;
  },

  select(it) {
    const r = el('div', 'row');
    const s = el('select', 'f');
    s.setAttribute('aria-label', it.label);
    s.style.flex = '0 1 220px';
    for (const o of it.options) {
      const op = el('option', '', o.label);
      op.value = o.value;
      if (o.value === it.value) op.selected = true;
      s.append(op);
    }
    s.addEventListener('change', () => set(it.id, s.value));
    r.append(txt(it.label, it.desc), s);
    return r;
  },

  combo(it) {
    const r = el('div', 'col');
    const lab = el('label', 'in-l', it.label);
    const f = el('div', 'field');
    const i = el('input', 'f');
    const list = el('datalist');
    list.id = 'dl-' + it.id;
    i.setAttribute('list', list.id);
    i.id = 'f-' + it.id;
    lab.htmlFor = i.id;
    i.value = it.value || '';
    i.spellcheck = false;
    const save = () => { if (i.value.trim()) set(it.id, i.value.trim()); };
    i.addEventListener('change', save);
    i.addEventListener('keydown', (e) => { if (e.key === 'Enter') { save(); i.blur(); } });
    f.append(i, list);
    r.append(lab, f);
    if (it.options_from) {
      window.tav.call('settings_set', { page: current, id: it.options_from, value: null })
        .then((res) => {
          for (const name of (res.options || [])) { const o = el('option'); o.value = name; list.append(o); }
        }).catch(() => {});
    }
    return r;
  },

  text(it) {
    const r = el('div', 'col');
    const lab = el('label', 'in-l', it.label);
    const f = el('div', 'field');
    const i = el(it.multiline ? 'textarea' : 'input', 'f');
    i.id = 'f-' + it.id;
    lab.htmlFor = i.id;
    if (!it.multiline) i.type = it.secret ? 'password' : 'text';
    if (it.multiline) i.rows = 3;
    i.value = it.value || '';
    i.placeholder = it.placeholder || '';
    i.spellcheck = !!it.multiline;
    i.disabled = !!it.disabled;
    const n = el('div', 'note');
    notes[it.id] = n;
    let saved = i.value;
    const save = () => {
      if (it.secret && !i.value.trim()) return;
      if (!it.secret && i.value === saved) return;
      saved = i.value;
      set(it.id, i.value).then((res) => { if (it.secret && !res.error) i.value = ''; });
    };
    f.append(i);
    if (it.button) {
      const b = el('button', 'btn', it.button);
      b.type = 'button';
      b.disabled = !!it.disabled;
      b.addEventListener('click', save);
      f.append(b);
    } else {
      i.addEventListener('blur', save);
    }
    if (!it.multiline) i.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
    r.append(lab, f);
    if (it.desc) r.append(el('div', 'ds', it.desc));
    r.append(n);
    return r;
  },

  button(it) {
    const r = el('div', 'col');
    const line = el('div', 'field');
    const b = el('button', 'btn' + (it.confirm ? ' danger' : ''), it.label);
    b.type = 'button';
    const n = el('div', 'note', '');
    notes[it.id] = n;
    b.addEventListener('click', () => {
      if (!it.confirm) return set(it.id, true);
      // Ask inline, not with a system dialog.
      line.replaceChildren();
      const yes = el('button', 'btn danger', 'Yes, ' + it.label.toLowerCase());
      const no = el('button', 'btn', 'Cancel');
      yes.type = no.type = 'button';
      yes.addEventListener('click', () => { set(it.id, true); line.replaceChildren(b); });
      no.addEventListener('click', () => { line.replaceChildren(b); note(it.id, ''); });
      line.append(yes, no);
      note(it.id, it.confirm, false);
    });
    line.append(b);
    if (it.note) line.append(el('span', 'ds', it.note));
    r.append(line, n);
    return r;
  },

  list(it) {
    const box = el('div', '');
    if (!it.items.length) {
      const r = el('div', 'col');
      r.append(el('div', 'info', it.empty || 'nothing'));
      box.append(r);
    }
    for (const x of it.items) {
      const r = el('div', 'row');
      r.append(txt(x.text, x.sub));
      const rm = el('button', 'link', it.remove || 'remove');
      rm.type = 'button';
      rm.addEventListener('click', () => set(it.id, it.id === 'memory' ? Number(x.key) : { remove: x.key }));
      r.append(rm);
      box.append(r);
    }
    if (it.add) {
      const r = el('div', 'col');
      const f = el('div', 'field');
      const i = el('input', 'f');
      i.placeholder = it.add;
      i.setAttribute('aria-label', it.add);
      const b = el('button', 'btn primary', 'Add');
      b.type = 'button';
      const n = el('div', 'note');
      notes[it.id] = n;
      const add = () => { if (i.value.trim()) set(it.id, { add: i.value.trim() }); };
      b.addEventListener('click', add);
      i.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
      f.append(i, b);
      r.append(f, n);
      box.append(r);
    }
    return box;
  },
};

// ---------------------------------------------------------------- pages
async function load(name, anim, keepScroll) {
  let page;
  try { page = await window.tav.call('settings_page', { name }); } catch (e) {
    body.replaceChildren(el('div', 'info bright', 'Could not load settings: ' + e.message));
    return;
  }
  const top = body.scrollTop;
  current = name;
  for (const k of Object.keys(notes)) delete notes[k];
  $('title').textContent = page.title;
  $('back').hidden = name === 'main';
  $('foot').textContent = page.footer || '';
  const frag = document.createDocumentFragment();
  for (const sec of page.sections) {
    if (sec.title) frag.append(el('div', 'eyebrow sec-title', sec.title));
    const g = el('div', 'grp');
    for (const it of sec.items) g.append((ROWS[it.type] || ROWS.info)(it));
    frag.append(g);
  }
  body.replaceChildren(frag);
  body.classList.remove('in', 'back');
  if (anim) { void body.offsetWidth; body.classList.add(anim); }
  body.scrollTop = keepScroll ? top : 0;
}

$('back').addEventListener('click', () => load('main', 'back'));
$('close').addEventListener('click', () => window.tav.closeMe());
$('sheet-close').addEventListener('click', () => { $('sheet').hidden = true; });
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!$('sheet').hidden) $('sheet').hidden = true;
  else if (current !== 'main') load('main', 'back');
  else window.tav.closeMe();
});

window.tav.on('backdrop', (pic) => {
  if (!pic) return;
  $('bd').style.backgroundImage = `url("${pic}")`;
  $('panel').classList.add('has-bd');
});

window.tav.on('events', (evs) => {
  for (const e of evs) {
    if (e.kind === 'note' && e.data && e.data.page === current) note(e.data.id, e.data.text, e.data.ok);
  }
});

load('main');
