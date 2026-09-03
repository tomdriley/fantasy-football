/* Draft-day client.
 *
 * Two rules shape this code:
 *   1. Never block the operator. Every network call has a timeout and a visible
 *      failure state; the interface keeps working from the last known board.
 *   2. Never lose an entry. Claims go to the server, which persists them, so a
 *      refresh or a crashed browser does not cost the draft.
 */
'use strict';

const $ = (id) => document.getElementById(id);

const App = {
  state: null,
  recs: [],
  polling: null,
  busy: false,
  fixPick: null,
  lastAdvice: 0,
};

/* ---------- transport ---------- */

async function api(path, opts = {}, timeoutMs = 12000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(path, { ...opts, signal: ctl.signal });
    const body = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, body };
  } catch (err) {
    return { ok: false, status: 0, body: { error: String(err.message || err) } };
  } finally {
    clearTimeout(timer);
  }
}

const post = (path, data) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data || {}),
  });

/* ---------- rendering ---------- */

function setConn(kind, text) {
  const el = $('conn');
  el.className = 'pill ' + kind;
  el.textContent = text;
}

function renderState(s) {
  App.state = s;
  $('mode').value = s.mode;
  if ($('seat').options.length <= 1) {
    for (let i = 1; i <= s.num_agents; i++) {
      const o = document.createElement('option');
      o.value = String(i);
      o.textContent = 'seat ' + i + (s.bot_seats.includes(i) ? ' (bot)' : '');
      $('seat').appendChild(o);
    }
  }
  $('seat').value = s.seat ? String(s.seat) : '';

  if (s.complete) {
    $('turn').textContent = 'Draft complete';
  } else if (s.my_turn) {
    $('turn').textContent = `YOUR PICK — round ${s.round}, pick ${s.current_pick}`;
  } else if (s.seat == null) {
    $('turn').textContent = 'Choose your seat to get advice';
  } else {
    $('turn').textContent = `Waiting — seat ${s.seat_on_clock} on the clock (pick ${s.current_pick})`;
  }
  $('clock').textContent = `${s.picks_made} / ${s.total_picks} picks`;

  if (s.mode === 'manual') setConn('manual', 'manual');
  else if (s.last_sync_error) setConn('offline', 'offline — using local board');
  else setConn('online', 'online');

  const gap = s.picks_until_my_turn;
  $('why').textContent = s.complete
    ? 'roster full'
    : gap === null ? 'no picks left'
    : gap === 0 ? 'you are on the clock'
    : `your next pick is ${s.next_pick_number} (${gap} away)`;

  renderSlots(s);
  renderRoster(s);
  renderRecent(s);
}

function renderSlots(s) {
  const counts = {};
  s.roster.forEach((p) => { counts[p.pos] = (counts[p.pos] || 0) + 1; });
  const el = $('slots');
  el.innerHTML = '';
  Object.entries(s.starting_slots).forEach(([pos, n]) => {
    if (pos === 'FLEX') return;
    const have = counts[pos] || 0;
    const d = document.createElement('span');
    d.className = 'slot ' + (have >= n ? 'filled' : 'empty');
    d.textContent = `${pos} ${have}/${n}`;
    el.appendChild(d);
  });

  const missing = Object.keys(s.unfilled || {});
  const w = $('warn');
  if (s.complete && missing.length) {
    w.textContent = `Roster is full but cannot field: ${missing.join(', ')}. Add a free agent before week 1.`;
    w.classList.remove('hidden');
  } else if (missing.length && s.picks_until_my_turn !== null) {
    const left = s.roster.length ? s.rounds - s.roster.length : s.rounds;
    if (left <= missing.length + 1) {
      w.textContent = `Only ${left} picks left and still need: ${missing.join(', ')}.`;
      w.classList.remove('hidden');
    } else { w.classList.add('hidden'); }
  } else { w.classList.add('hidden'); }
}

function renderRoster(s) {
  const el = $('roster');
  el.innerHTML = '';
  if (!s.roster.length) {
    el.innerHTML = '<li class="hint">no players yet</li>';
    return;
  }
  s.roster.forEach((p) => {
    const li = document.createElement('li');
    li.innerHTML = `<span class="rpos">${p.pos}</span><span>${escapeHtml(p.name)}</span>`;
    el.appendChild(li);
  });
}

function renderRecent(s) {
  const el = $('recent');
  el.innerHTML = '';
  if (!s.recent.length) {
    el.innerHTML = '<li class="hint">no picks recorded yet</li>';
    return;
  }
  const mine = new Set();
  s.recent.forEach((r) => { if (s.seat && r.seat === s.seat) mine.add(r.pick); });
  [...s.recent].reverse().forEach((r) => {
    const li = document.createElement('li');
    li.innerHTML =
      `<span class="pk">${r.pick}</span>` +
      `<span class="who ${mine.has(r.pick) ? 'mine' : ''}">${escapeHtml(r.name)} ` +
      `<span class="src">${r.pos} · seat ${r.seat} · ${r.source}</span></span>`;
    const fix = document.createElement('button');
    fix.className = 'ghost';
    fix.textContent = 'Fix';
    fix.onclick = () => openFix(r.pick);
    li.appendChild(fix);
    el.appendChild(li);
  });
}

function renderPicks(list, opts = {}) {
  const el = $('picks');
  el.innerHTML = '';
  if (!list || !list.length) {
    el.innerHTML = '<li class="hint">no candidates</li>';
    return;
  }
  list.forEach((p, i) => {
    const li = document.createElement('li');
    if (i === 0) li.className = 'top';
    li.innerHTML =
      `<span class="rank">${i + 1}</span>` +
      `<span><span class="name">${escapeHtml(p.name)}</span></span>` +
      `<span class="pos">${p.pos}</span>` +
      `<span class="num">value ${p.vor}</span>` +
      `<span class="num">adp ${p.adp ?? '—'}</span>`;
    const btn = document.createElement('button');
    btn.className = 'take';
    btn.textContent = i === 0 ? 'TAKE' : 'take';
    btn.onclick = () => claimById(p.player_id);
    li.appendChild(btn);
    el.appendChild(li);
  });

  const s = App.state;
  const spread = list.length > 2 && list[0].ev != null && list[2].ev != null
    ? list[0].ev - list[2].ev : null;
  const st = $('stakes');
  if (opts.panic) { st.textContent = 'instant list — no simulation'; st.className = 'tag hot'; }
  else if (spread === null) { st.textContent = ''; st.className = 'tag'; }
  else if (spread < 8) { st.textContent = `stakes LOW (${spread.toFixed(0)} pts) — decide fast`; st.className = 'tag'; }
  else if (spread > 25) { st.textContent = `stakes HIGH (${spread.toFixed(0)} pts)`; st.className = 'tag hot'; }
  else { st.textContent = `stakes medium (${spread.toFixed(0)} pts)`; st.className = 'tag'; }

  const sa = $('sanity');
  const top = list[0];
  if (!s || top.adp == null) { sa.textContent = ''; sa.className = 'tag'; }
  else {
    const delta = top.adp - s.current_pick;
    if (delta < -12) { sa.textContent = `unusual — market ranks him ${Math.abs(delta).toFixed(0)} picks earlier`; sa.className = 'tag bad'; }
    else if (delta > 25) { sa.textContent = `reach — market ranks him ${delta.toFixed(0)} picks later`; sa.className = 'tag hot'; }
    else { sa.textContent = `sanity ok — within ${Math.abs(delta).toFixed(0)} of consensus`; sa.className = 'tag'; }
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function status(text, cls) {
  $('status').textContent = text;
  $('status').className = cls || '';
}

/* ---------- actions ---------- */

async function refreshState() {
  const r = await api('/api/state');
  if (r.ok) renderState(r.body);
  return r.ok;
}

async function refreshAdvice(force) {
  const s = App.state;
  if (!s || s.seat == null || s.complete) return;
  const now = Date.now();
  if (!force && now - App.lastAdvice < 1500) return;
  App.lastAdvice = now;
  status('thinking…');
  const r = await api('/api/recommend', {}, 30000);
  if (r.ok && r.body.picks) {
    App.recs = r.body.picks;
    renderPicks(App.recs);
    status('ready');
  } else {
    const p = await api('/api/panic');
    if (p.ok) { renderPicks(p.body.picks, { panic: true }); status('degraded — showing instant list'); }
    else status('advice unavailable', 'err');
  }
}

async function doSync() {
  if (App.state && App.state.mode === 'manual') return;
  const r = await post('/api/sync');
  if (r.body && r.body.state) renderState(r.body.state);
  if (r.body && r.body.ok === false) setConn('offline', 'offline — using local board');
  return r;
}

async function claimById(playerId) {
  if (App.busy) return;
  App.busy = true;
  const r = await post('/api/claim', { player_id: playerId });
  App.busy = false;
  if (r.body && r.body.state) renderState(r.body.state);
  if (r.body && r.body.ok === false) {
    entryMsg(r.body.error || 'could not record that pick', true);
  } else {
    entryMsg('recorded ' + (r.body.item ? r.body.item.name : ''), false);
    $('q').value = '';
    $('suggest').innerHTML = '';
    $('panicOut').classList.add('hidden');
  }
  refreshAdvice(true);
}

async function claimByQuery(q) {
  if (!q.trim() || App.busy) return;
  App.busy = true;
  const r = await post('/api/claim', { query: q.trim() });
  App.busy = false;
  if (r.body && r.body.state) renderState(r.body.state);

  if (r.body && r.body.ambiguous) {
    const box = $('suggest');
    box.innerHTML = '';
    (r.body.results || []).forEach((p) => {
      const b = document.createElement('button');
      b.innerHTML = `${escapeHtml(p.name)}<span class="s-pos">${p.pos} · adp ${p.adp ?? '—'}</span>`;
      b.onclick = () => claimById(p.player_id);
      box.appendChild(b);
    });
    entryMsg(r.body.error || 'pick one', true);
    return;
  }
  if (r.body && r.body.ok === false) { entryMsg(r.body.error || 'not recorded', true); return; }
  entryMsg('recorded ' + (r.body.item ? r.body.item.name : ''), false);
  $('q').value = '';
  $('suggest').innerHTML = '';
  refreshAdvice(true);
}

function entryMsg(text, isErr) {
  const el = $('entryMsg');
  el.textContent = text;
  el.className = 'msg ' + (isErr ? 'err' : 'ok');
}

async function doPanic() {
  const r = await api('/api/panic', {}, 5000);
  if (r.ok && r.body.picks && r.body.picks.length) {
    renderPicks(r.body.picks, { panic: true });
    const top = r.body.picks[0];
    const out = $('panicOut');
    out.innerHTML = `Take <b>${escapeHtml(top.name)}</b> (${top.pos}). ` +
      `If he is already gone, take the next one down.`;
    out.classList.remove('hidden');
    status('panic list shown');
  } else {
    status('panic failed — use the printed sheet', 'err');
  }
}

async function doUndo() {
  const r = await post('/api/undo');
  if (r.body && r.body.state) renderState(r.body.state);
  entryMsg(r.body && r.body.item ? 'undid ' + r.body.item.name : 'nothing to undo', false);
  refreshAdvice(true);
}

/* ---------- fix dialog ---------- */

function openFix(pick) {
  App.fixPick = pick;
  $('fixPick').textContent = String(pick);
  $('fixQuery').value = '';
  $('fixSuggest').innerHTML = '';
  $('fixDialog').classList.remove('hidden');
  $('fixQuery').focus();
}

function closeFix() {
  App.fixPick = null;
  $('fixDialog').classList.add('hidden');
}

async function fixSearch(q) {
  if (!q.trim()) { $('fixSuggest').innerHTML = ''; return; }
  const r = await api('/api/search?q=' + encodeURIComponent(q.trim()));
  const box = $('fixSuggest');
  box.innerHTML = '';
  ((r.body && r.body.results) || []).forEach((p) => {
    const b = document.createElement('button');
    b.innerHTML = `${escapeHtml(p.name)}<span class="s-pos">${p.pos} · adp ${p.adp ?? '—'}</span>`;
    b.onclick = () => applyFix(p.player_id);
    box.appendChild(b);
  });
}

async function applyFix(playerId) {
  const r = await post('/api/correct', { pick: App.fixPick, player_id: playerId });
  if (r.body && r.body.state) renderState(r.body.state);
  if (r.body && r.body.ok === false) { entryMsg(r.body.error, true); return; }
  closeFix();
  entryMsg('fixed pick ' + (r.body.item ? '→ ' + r.body.item.name : ''), false);
  refreshAdvice(true);
}

async function removeFix() {
  const r = await post('/api/remove', { pick: App.fixPick });
  if (r.body && r.body.state) renderState(r.body.state);
  closeFix();
  entryMsg('removed a pick; later picks shifted up', false);
  refreshAdvice(true);
}

/* ---------- wiring ---------- */

function startPolling() {
  if (App.polling) clearInterval(App.polling);
  App.polling = setInterval(async () => {
    if (!App.state || App.state.mode === 'manual') return;
    const before = App.state.picks_made;
    await doSync();
    if (App.state && App.state.picks_made !== before) refreshAdvice(true);
  }, 5000);
}

function bind() {
  $('add').onclick = () => claimByQuery($('q').value);
  $('q').onkeydown = (e) => { if (e.key === 'Enter') claimByQuery($('q').value); };
  $('undo').onclick = doUndo;
  $('panic').onclick = doPanic;
  $('sync').onclick = async () => { await doSync(); refreshAdvice(true); };
  $('reset').onclick = async () => {
    if (!confirm('Clear every recorded pick? This cannot be undone.')) return;
    const r = await post('/api/reset');
    if (r.body && r.body.state) renderState(r.body.state);
    refreshAdvice(true);
  };
  $('mode').onchange = async () => {
    const r = await post('/api/mode', { mode: $('mode').value });
    if (r.body && r.body.state) renderState(r.body.state);
    refreshAdvice(true);
  };
  $('seat').onchange = async () => {
    const v = $('seat').value;
    const r = await post('/api/seat', { seat: v ? Number(v) : null });
    if (r.body && r.body.state) renderState(r.body.state);
    refreshAdvice(true);
  };
  $('fixCancel').onclick = closeFix;
  $('fixApply').onclick = () => fixSearch($('fixQuery').value);
  $('fixRemove').onclick = removeFix;
  $('fixQuery').oninput = (e) => fixSearch(e.target.value);
  $('fixQuery').onkeydown = (e) => { if (e.key === 'Enter') fixSearch(e.target.value); };

  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.key === 'p' || e.key === 'P') { e.preventDefault(); doPanic(); }
    if (e.key === 'u' || e.key === 'U') { e.preventDefault(); doUndo(); }
    if (e.key === 'Escape') closeFix();
  });
}

async function main() {
  bind();
  const ok = await refreshState();
  if (!ok) { status('cannot reach the local server', 'err'); return; }
  if (App.state.mode !== 'manual') await doSync();
  await refreshAdvice(true);
  startPolling();
  status('ready');
}

main();
