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
  setupSeat: null,
  searchTimer: null,
  adviceTimer: null,
  lastQuery: null,
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

/* ---------- setup screen ---------- */

/* The first thing the operator sees. It asks the two questions the engine
 * cannot answer for itself -- which seat is yours, and where picks come from --
 * rather than guessing and being confidently wrong about both. */

function openSetup(canCancel) {
  const s = App.state;
  if (!s) return;
  App.setupSeat = s.seat || null;

  const grid = $('setupSeats');
  grid.innerHTML = '';
  for (let i = 1; i <= s.num_agents; i++) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'seat-btn' + (App.setupSeat === i ? ' on' : '');
    b.textContent = i;
    if (s.bot_seats.includes(i)) b.title = 'currently an unclaimed seat';
    b.onclick = () => { App.setupSeat = i; paintSetupSeats(); };
    grid.appendChild(b);
  }
  const unk = document.createElement('button');
  unk.type = 'button';
  unk.className = 'seat-btn wide-btn' + (App.setupSeat === null ? ' on' : '');
  unk.textContent = 'Not drawn yet';
  unk.onclick = () => { App.setupSeat = null; paintSetupSeats(); };
  grid.appendChild(unk);

  const want = s.mode || 'manual';
  document.querySelectorAll('input[name=setupMode]').forEach((r) => {
    r.checked = r.value === want;
  });

  $('setupCancel').classList.toggle('hidden', !canCancel);
  $('setup').classList.remove('hidden');
  paintSetupSeats();
}

function paintSetupSeats() {
  const btns = $('setupSeats').querySelectorAll('.seat-btn');
  btns.forEach((b) => {
    const isUnknown = b.classList.contains('wide-btn');
    const on = isUnknown ? App.setupSeat === null : Number(b.textContent) === App.setupSeat;
    b.classList.toggle('on', on);
  });
  $('setupSeatMsg').textContent = App.setupSeat === null
    ? 'Advice will be best-available only until you set a seat.'
    : `Seat ${App.setupSeat}.`;
  $('setupSeatMsg').className = 'msg' + (App.setupSeat === null ? ' warn-text' : '');
}

async function applySetup() {
  const mode = (document.querySelector('input[name=setupMode]:checked') || {}).value || 'manual';
  const r = await post('/api/start', { seat: App.setupSeat, mode });
  if (r.body && r.body.state) renderState(r.body.state);
  $('setup').classList.add('hidden');
  if (mode !== 'manual') await doSync();
  await refreshAdvice(true);
  status('ready');
}

async function goManual() {
  if (!App.state || App.state.mode === 'manual') return;
  const r = await post('/api/manual');
  if (r.body && r.body.state) renderState(r.body.state);
  entryMsg('manual mode — polling stopped, your board is untouched', false);
  status('manual');
}

/* ---------- recording picks ---------- */

/* Recording an opponent's pick is the time-critical path: between two of our
 * own turns up to eighteen picks can land, and nine autopicking opponents take
 * only seconds. So the design is click-first -- the likely players are already
 * on screen, and typing narrows a visible list rather than submitting a guess
 * the server resolves out of sight. */

/* Names are shown as the platform shows them -- "J. Gibbs" -- so matching this
 * screen against the draft room is a glance rather than a read. The server
 * falls back to the full name where an abbreviation would be ambiguous. */
const disp = (p) => p.short || p.name || '';

function pickButton(p, cls) {
  const b = document.createElement('button');
  b.className = cls;
  b.innerHTML =
    `<span class="p-name">${escapeHtml(disp(p))}</span>` +
    `<span class="p-meta">${p.pos}${p.team ? ' · ' + escapeHtml(p.team) : ''}` +
    `${p.adp != null ? ' · adp ' + p.adp : ''}</span>`;
  b.onclick = () => claimById(p.player_id);
  return b;
}

function renderQuick(s) {
  const box = $('quick');
  box.innerHTML = '';
  (s.quick || []).forEach((p) => box.appendChild(pickButton(p, 'quick-btn')));
}

function renderSuggestions(results) {
  const box = $('suggest');
  box.innerHTML = '';
  if (!results.length) return;
  results.forEach((p) => box.appendChild(pickButton(p, 'sug-btn')));
}

/* A short debounce: long enough that a fast typist issues one request per word
 * rather than per keystroke, short enough to feel immediate. */
function onQueryInput() {
  const q = $('q').value.trim();
  if (q === App.lastQuery) return;
  App.lastQuery = q;
  clearTimeout(App.searchTimer);
  if (!q) { $('suggest').innerHTML = ''; return; }
  App.searchTimer = setTimeout(async () => {
    const r = await api('/api/suggest?limit=8&q=' + encodeURIComponent(q), {}, 6000);
    if ($('q').value.trim() !== q) return;   // a later keystroke already won
    if (r.ok) renderSuggestions(r.body.results || []);
  }, 70);
}

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

  // The escape hatch is only worth screen space when there is something to
  // escape from; in manual mode it would just be noise.
  $('goManual').classList.toggle('hidden', s.mode === 'manual');

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
    : s.seat == null ? 'set your seat above to see when you pick'
    : gap === null ? 'you have no picks left'
    : gap === 0 ? 'you are on the clock'
    : `your next pick is ${s.next_pick_number} (${gap} away)`;

  // A board that is not empty at startup is either a legitimate resume or
  // leftover state. The operator cannot tell which, so say so explicitly the
  // first time rather than letting it look like the draft already began.
  if (!App.greeted) {
    App.greeted = true;
    if (s.picks_made > 0) {
      const box = $('proposal');
      $('proposalText').innerHTML =
        `Resumed a board with <b>${s.picks_made}</b> pick(s) already recorded. ` +
        `If this draft has not started yet, clear it.`;
      $('acceptProposal').textContent = 'Keep it';
      $('dismissProposal').textContent = 'Clear board';
      box.classList.remove('hidden');
      App.resumePrompt = true;
    }
  }

  renderSlots(s);
  renderRoster(s);
  renderRecent(s);
  renderQuick(s);
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
    li.innerHTML = `<span class="rpos">${p.pos}</span><span>${escapeHtml(disp(p))}</span>`;
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
      `<span class="who ${mine.has(r.pick) ? 'mine' : ''}">${escapeHtml(disp(r))} ` +
      `<span class="src">${r.pos} · seat ${r.seat} · ${r.source}</span></span>`;
    const fix = document.createElement('button');
    fix.className = 'ghost';
    fix.textContent = 'Fix';
    fix.onclick = () => openFix(r.pick);
    li.appendChild(fix);
    el.appendChild(li);
  });
}

function setPicksStale(stale) {
  const el = $('picks');
  el.classList.toggle('stale', !!stale);
  el.querySelectorAll('button').forEach((b) => { b.disabled = !!stale; });
}

function renderPicks(list, opts = {}) {
  const el = $('picks');
  el.classList.remove('stale');
  el.innerHTML = '';
  if (!list || !list.length) {
    el.innerHTML = '<li class="hint">no candidates</li>';
    return;
  }
  // Alternatives are frequently near-tied on expected value while differing
  // enormously in scarcity. The engine's top choice is stable across seeds, but
  // the runners-up are noise, and a player with far lower value-over-
  // replacement sitting at rank two is the classic trap this project exists to
  // avoid. Flag it rather than hide it: the operator cannot judge it unaided.
  const topVor = list[0].vor || 0;
  list.forEach((p, i) => {
    const li = document.createElement('li');
    const weak = i > 0 && topVor > 0 && p.vor < 0.6 * topVor;
    li.className = (i === 0 ? 'top' : '') + (weak ? ' weak' : '');
    li.innerHTML =
      `<span class="rank">${i + 1}</span>` +
      `<span><span class="name">${escapeHtml(disp(p))}</span>` +
      (weak ? '<span class="flag">low value — much easier to replace later</span>' : '') +
      `</span>` +
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
  if (!s || s.complete) return;
  const now = Date.now();
  if (!force && now - App.lastAdvice < 1500) return;
  App.lastAdvice = now;

  // Before the draft order is drawn there is no seat, so the optimizer cannot
  // reason about who picks when. A best-available ordering is still the right
  // thing to look at, so show it -- but say plainly that it is not a plan, and
  // let it upgrade by itself the moment the seat is known.
  if (s.seat == null) {
    const p = await api('/api/panic');
    if (p.ok) {
      renderPicks(p.body.picks, { panic: true });
      status('seat unknown — best available only, not a draft plan', 'warn');
    }
    return;
  }

  setPicksStale(true);
  status('thinking…');
  const r = await api('/api/recommend', {}, 30000);
  if (r.ok && r.body.picks) {
    App.recs = r.body.picks;
    renderPicks(App.recs, { panic: !!r.body.degraded });
    status(r.body.degraded
      ? 'seat unknown — best available only, not a draft plan'
      : 'ready', r.body.degraded ? 'warn' : undefined);
  } else {
    const p = await api('/api/panic');
    if (p.ok) { renderPicks(p.body.picks, { panic: true }); status('degraded — showing instant list'); }
    else status('advice unavailable', 'err');
  }
}

async function doSync() {
  if (!App.state || App.state.mode === 'manual') return;

  // Assisted mode proposes rather than applies: the operator keeps control of a
  // board they may have been maintaining by hand, and a lagging or wrong feed
  // cannot silently overwrite it.
  if (App.state.mode === 'assisted') return proposeFromFeed();

  const r = await post('/api/sync');
  if (r.body && r.body.state) renderState(r.body.state);
  if (r.body && r.body.ok === false) setConn('offline', 'offline — using local board');
  return r;
}

async function proposeFromFeed() {
  const r = await api('/api/propose');
  const body = (r.body) || {};
  if (body.state) renderState(body.state);
  if (body.ok === false) { setConn('offline', 'offline — using local board'); return r; }

  const box = $('proposal');
  const adds = body.additions || [];
  const conflicts = body.conflicts || [];
  if (!adds.length && !conflicts.length) { box.classList.add('hidden'); return r; }

  const names = adds.slice(0, 6).map((a) => `${a.pick}. ${escapeHtml(disp(a))}`).join(', ');
  let html = adds.length
    ? `The feed has <b>${adds.length}</b> pick(s) you do not: ${names}${adds.length > 6 ? '…' : ''}`
    : '';
  if (conflicts.length) {
    const c = conflicts[0];
    html += `<div class="conflict">Disagreement at pick ${c.pick}: ` +
      `you have ${escapeHtml(disp(c.local))}, the feed says ${escapeHtml(disp(c.remote))}.</div>`;
  }
  $('proposalText').innerHTML = html;
  box.classList.remove('hidden');
  return r;
}

async function claimById(playerId) {
  if (App.busy) return;
  App.busy = true;
  const r = await post('/api/claim', { player_id: playerId });
  App.busy = false;
  if (r.body && r.body.state) renderState(r.body.state);
  if (r.body && r.body.ok === false) {
    setPicksStale(false);
    entryMsg(r.body.error || 'could not record that pick', true);
  } else {
    entryMsg('recorded ' + (r.body.item ? r.body.item.name : ''), false);
    $('q').value = '';
    App.lastQuery = '';
    $('suggest').innerHTML = '';
    $('panicOut').classList.add('hidden');
  }
  scheduleAdvice();
}

/* Advice costs a second or two. Recomputing it after every entry would make a
 * burst of nine opponent picks queue up behind nine simulations, so it is
 * coalesced -- except when we are on the clock, where it is the only thing
 * that matters. */
function scheduleAdvice() {
  clearTimeout(App.adviceTimer);
  const s = App.state;
  const urgent = s && (s.my_turn || (s.picks_until_my_turn != null && s.picks_until_my_turn <= 1));
  if (urgent) { setPicksStale(true); refreshAdvice(true); return; }
  setPicksStale(true);
  App.adviceTimer = setTimeout(() => refreshAdvice(true), 700);
}

/* Enter no longer submits a raw query. The server used to resolve a partial
 * name out of sight and could commit the wrong player -- observed live, where
 * correcting it cost far more time than the entry saved. Enter now just shows
 * the matches immediately; committing is always a click on a visible name. */
async function showMatchesNow() {
  clearTimeout(App.searchTimer);
  const q = $('q').value.trim();
  if (!q) return;
  const r = await api('/api/suggest?limit=8&q=' + encodeURIComponent(q), {}, 6000);
  if (r.ok) {
    const results = r.body.results || [];
    renderSuggestions(results);
    if (!results.length) entryMsg('no player matches "' + q + '"', true);
  }
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
  fixMsg('');
  $('fixDialog').classList.remove('hidden');
  $('fixQuery').focus();
}

function closeFix() {
  App.fixPick = null;
  $('fixDialog').classList.add('hidden');
}

async function fixSearch(q) {
  if (!q.trim()) { $('fixSuggest').innerHTML = ''; return []; }
  const r = await api('/api/search?q=' + encodeURIComponent(q.trim()));
  const results = (r.body && r.body.results) || [];
  const box = $('fixSuggest');
  box.innerHTML = '';
  results.forEach((p) => {
    const b = document.createElement('button');
    b.innerHTML = `${escapeHtml(disp(p))} <span class="s-pos">${p.pos} · adp ${p.adp ?? '—'}</span>`;
    b.onclick = () => applyFix(p.player_id);
    box.appendChild(b);
  });
  return results;
}

/* The Replace button must actually replace. Searching and waiting for a second
 * click looks identical to a button that does nothing, which under a running
 * clock is worse than no button at all. An unambiguous name is applied
 * immediately; anything else falls back to offering the choices. */
async function applyFixFromQuery() {
  const q = $('fixQuery').value;
  if (!q.trim()) { fixMsg('type a name to replace this pick'); return; }
  const results = await fixSearch(q);
  if (results.length === 1) { await applyFix(results[0].player_id); return; }
  fixMsg(results.length ? 'several match — pick one below' : `no match for "${q.trim()}"`);
}

function fixMsg(text) {
  const el = $('fixMsg');
  if (el) el.textContent = text;
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

/* Poll faster as our turn approaches. A fixed five-second interval meant a
 * pick could sit unseen for five seconds, which reads as the tool lagging the
 * room. Syncing is cheap (about a tenth of a second), so the rate is raised
 * when it matters and kept low when it does not, rather than hammering the API
 * for the whole draft. */
function pollInterval() {
  const s = App.state;
  if (!s || s.complete) return 10000;
  const gap = s.picks_until_my_turn;
  if (gap === null) return 10000;
  if (gap <= 2) return 1500;
  if (gap <= 5) return 2500;
  return 5000;
}

function startPolling() {
  if (App.polling) clearTimeout(App.polling);
  const tick = async () => {
    try {
      // Nothing may touch the board while the operator is still telling us who
      // they are -- a sync now could attribute picks against a seat they are
      // about to change.
      const setupOpen = !$('setup').classList.contains('hidden');
      if (!setupOpen && App.state && App.state.mode !== 'manual') {
        const before = App.state.picks_made;
        await doSync();
        if (App.state && App.state.picks_made !== before) await refreshAdvice(true);
      }
    } finally {
      App.polling = setTimeout(tick, pollInterval());
    }
  };
  App.polling = setTimeout(tick, pollInterval());
}

function bind() {
  // Several event types, because the value can change without an `input`
  // event: pasting, autofill, and programmatic entry all reach the box by
  // different routes, and a search box that silently does nothing is exactly
  // the failure this was built to remove.
  ['input', 'keyup', 'paste', 'change'].forEach((evt) => {
    $('q').addEventListener(evt, () => setTimeout(onQueryInput, 0));
  });
  $('q').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); showMatchesNow(); } };
  $('clearQ').onclick = () => {
    $('q').value = '';
    App.lastQuery = '';
    $('suggest').innerHTML = '';
    entryMsg('', false);
    $('q').focus();
  };
  $('undo').onclick = doUndo;
  $('panic').onclick = doPanic;
  $('goManual').onclick = goManual;
  $('openSetup').onclick = () => openSetup(true);
  $('setupStart').onclick = applySetup;
  $('setupCancel').onclick = () => $('setup').classList.add('hidden');
  $('sync').onclick = async () => { await doSync(); refreshAdvice(true); };
  $('reset').onclick = async () => {
    if (!confirm('Clear every recorded pick? This cannot be undone.\n\n'
                 + 'Your seat and mode are kept.')) return;
    const r = await post('/api/reset');
    if (r.body && r.body.state) renderState(r.body.state);
    entryMsg('board cleared — seat and mode kept', false);
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
  $('acceptProposal').onclick = async () => {
    if (App.resumePrompt) {
      App.resumePrompt = false;
      $('proposal').classList.add('hidden');
      return;
    }
    const r = await post('/api/sync');
    if (r.body && r.body.state) renderState(r.body.state);
    $('proposal').classList.add('hidden');
    refreshAdvice(true);
  };
  $('dismissProposal').onclick = async () => {
    if (App.resumePrompt) {
      App.resumePrompt = false;
      const r = await post('/api/reset');
      if (r.body && r.body.state) renderState(r.body.state);
      entryMsg('board cleared', false);
      refreshAdvice(true);
    }
    $('proposal').classList.add('hidden');
  };
  $('fixCancel').onclick = closeFix;
  $('fixApply').onclick = applyFixFromQuery;
  $('fixRemove').onclick = removeFix;
  $('fixQuery').oninput = (e) => fixSearch(e.target.value);
  $('fixQuery').onkeydown = (e) => { if (e.key === 'Enter') applyFixFromQuery(); };

  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.key === 'p' || e.key === 'P') { e.preventDefault(); doPanic(); }
    if (e.key === 'u' || e.key === 'U') { e.preventDefault(); doUndo(); }
    if (e.key === 'm' || e.key === 'M') { e.preventDefault(); goManual(); }
    if (e.key === 'Escape') { closeFix(); }
  });
}

async function main() {
  bind();
  const ok = await refreshState();
  if (!ok) { status('cannot reach the local server', 'err'); return; }

  // Ask before doing anything. Polling a feed or offering advice while the seat
  // is still a guess is how a board silently ends up describing someone else's
  // draft.
  if (!App.state.configured) {
    openSetup(false);
    status('choose your seat and mode to begin');
    startPolling();
    return;
  }

  if (App.state.mode !== 'manual') await doSync();
  await refreshAdvice(true);
  startPolling();
  status('ready');
}

main();
