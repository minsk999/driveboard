/* DriveBoard — 프로젝트 보기 (SPEC-2 B.4). vanilla JS.
 * 계약: window.Projects.mount(containerEl, ctx) -> { unmount(), refresh(), onState(state) }
 * ctx = { api(path, opts), fmtSize, fmtAgo, fmtDate, catColor, cats, drives(), select:{set,clear,onChange},
 *         openMove(items, mode), openRecycle(items), reveal(path), toast(msg, opts), openSettings(section) }
 * api opts = { method:'GET'|'POST', body:<객체> } — 쿼리는 경로에 붙여 보낸다.
 */
'use strict';
(() => {

/* ═════════════════════════════════════════════ 기본 도구 */
const NCAT = 11;
const RMQ = window.matchMedia ? matchMedia('(prefers-reduced-motion: reduce)') : null;
let reduced = !!(RMQ && RMQ.matches);
if (RMQ && RMQ.addEventListener) RMQ.addEventListener('change', e => { reduced = e.matches; });

function h(tag, a, ...kids) {
  const el = document.createElement(tag);
  if (a) {
    for (const k in a) {
      const v = a[k];
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'html') el.innerHTML = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'style' && typeof v === 'object') { for (const sk in v) { if (sk.startsWith('--')) el.style.setProperty(sk, v[sk]); else el.style[sk] = v[sk]; } }
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  if (tag === 'button' && a && a['data-tip'] && !a['aria-label']) el.setAttribute('aria-label', a['data-tip']);
  return el;
}
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const raf2 = fn => requestAnimationFrame(() => requestAnimationFrame(fn));
const store = {
  get(k, d) { try { const v = localStorage.getItem('db.pj.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('db.pj.' + k, JSON.stringify(v)); } catch (e) { /* 저장 못 해도 동작 */ } },
};
const inField = e => { const t = e.target; return !!(t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))); };

function fmtTime(s) {
  s = Math.max(0, Math.floor(Number(s) || 0));
  const hh = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return hh ? `${hh}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`;
}
function ymd(d) { const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(d || ''); return m ? [+m[1], +m[2], m[3] ? +m[3] : 0] : null; }
function fmtYMD(d, long) {
  const v = ymd(d);
  if (!v) return '';
  if (!v[2]) return long ? `${v[0]}년 ${v[1]}월` : `${v[0]}. ${v[1]}`;   // 이름에 연·월만 있는 폴더(2026-09_…)
  return long ? `${v[0]}년 ${v[1]}월 ${v[2]}일` : `${v[0]}. ${v[1]}. ${v[2]}`;
}
function fmtTs(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}. ${d.getMonth() + 1}. ${d.getDate()}`;
}
function dateCut(days) {
  const d = new Date(Date.now() - days * 864e5);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function scrollParent(el) {
  for (let p = el && el.parentElement; p && p !== document.body; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY;
    if (o === 'auto' || o === 'scroll') return p;
  }
  return document.scrollingElement || document.documentElement;
}

/* 아이콘 — app.js 와 같은 선 굵기·모양 */
const P = {
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  chevR: '<path d="M9.5 6l6 6-6 6"/>',
  chevL: '<path d="M14.5 6l-6 6 6 6"/>',
  chevD: '<path d="M6.5 9.5l5.5 5.5 5.5-5.5"/>',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  reveal: '<path d="M3.5 7.4c0-1.1.9-2 2-2h3.7l2 2.1h7.3c1.1 0 2 .9 2 2v7.6c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2z"/><path d="M10 13.5h5M12.8 11.2l2.3 2.3-2.3 2.3"/>',
  folder: '<path d="M3.5 7.4c0-1.1.9-2 2-2h3.7l2 2.1h7.3c1.1 0 2 .9 2 2v7.6c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2z"/>',
  folderPlus: '<path d="M3.5 7.4c0-1.1.9-2 2-2h3.7l2 2.1h7.3c1.1 0 2 .9 2 2v7.6c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2z"/><path d="M12 11v5M9.5 13.5h5"/>',
  move: '<path d="M3.5 12h11"/><path d="M11 8l4 4-4 4"/><path d="M14.5 4.5h3a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-3"/>',
  archive: '<rect x="3.5" y="4.5" width="17" height="4.6" rx="1.6"/><path d="M5 9.1v8.7A2.2 2.2 0 0 0 7.2 20h9.6a2.2 2.2 0 0 0 2.2-2.2V9.1"/><path d="M10 13h4"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.2"/><path d="M15.5 8.5V6.2a1.7 1.7 0 0 0-1.7-1.7H6.2a1.7 1.7 0 0 0-1.7 1.7v7.6c0 .9.8 1.7 1.7 1.7h2.3"/>',
  trash: '<path d="M4.5 7h15"/><path d="M9.5 7V5.3c0-.7.5-1.3 1.2-1.3h2.6c.7 0 1.2.6 1.2 1.3V7"/><path d="M6.6 7l.8 11.1A2 2 0 0 0 9.4 20h5.2a2 2 0 0 0 2-1.9L17.4 7"/><path d="M10.2 11v5M13.8 11v5"/>',
  notp: '<circle cx="12" cy="12" r="8.5"/><path d="M6 6l12 12"/>',
  play: '<path d="M8 5.6v12.8a.9.9 0 0 0 1.37.77l10.2-6.4a.9.9 0 0 0 0-1.54L9.37 4.83A.9.9 0 0 0 8 5.6z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6.5" y="5" width="3.8" height="14" rx="1.2" fill="currentColor" stroke="none"/><rect x="13.7" y="5" width="3.8" height="14" rx="1.2" fill="currentColor" stroke="none"/>',
  vol: '<path d="M4.5 9.5v5h3.2l4.3 3.7V5.8L7.7 9.5z" fill="currentColor" stroke="none"/><path d="M15.5 9a4.2 4.2 0 0 1 0 6"/><path d="M18 6.5a7.6 7.6 0 0 1 0 11"/>',
  mute: '<path d="M4.5 9.5v5h3.2l4.3 3.7V5.8L7.7 9.5z" fill="currentColor" stroke="none"/><path d="M15.5 9.5l5 5M20.5 9.5l-5 5"/>',
  fs: '<path d="M4.5 9V5.8c0-.7.6-1.3 1.3-1.3H9M15 4.5h3.2c.7 0 1.3.6 1.3 1.3V9M19.5 15v3.2c0 .7-.6 1.3-1.3 1.3H15M9 19.5H5.8c-.7 0-1.3-.6-1.3-1.3V15"/>',
  fsx: '<path d="M9 4.5V8c0 .6-.4 1-1 1H4.5M15 4.5V8c0 .6.4 1 1 1h3.5M15 19.5V16c0-.6.4-1 1-1h3.5M9 19.5V16c0-.6-.4-1-1-1H4.5"/>',
  film: '<rect x="3.5" y="5.5" width="17" height="13" rx="2.6"/><path d="M10.4 9.4v5.2l4.3-2.6z"/>',
  nofilm: '<rect x="3.5" y="5.5" width="17" height="13" rx="2.6"/><path d="M5 20L19 4"/>',
  drive: '<rect x="3.5" y="6" width="17" height="12" rx="2.6"/><path d="M7 10h4.5M7 14h2.5"/><circle cx="16.5" cy="12" r="1.3"/>',
  people: '<circle cx="9" cy="8.5" r="3.2"/><path d="M3.5 19c.6-3 2.7-4.7 5.5-4.7s4.9 1.7 5.5 4.7"/><path d="M15.5 5.6a3 3 0 0 1 0 5.8M17.6 14.5c1.6.6 2.6 2 2.9 4.5"/>',
  cal: '<rect x="4" y="5.5" width="16" height="14.5" rx="2.6"/><path d="M4 10h16M8.5 3.5v3.5M15.5 3.5v3.5"/>',
  sort: '<path d="M8 5v14M4.5 15.5L8 19l3.5-3.5"/><path d="M16 19V5M12.5 8.5L16 5l3.5 3.5"/>',
  merge: '<path d="M6 4.5v4.2a4 4 0 0 0 1.2 2.8L12 16.3V20"/><path d="M18 4.5v4.2a4 4 0 0 1-1.2 2.8L14.3 14"/><path d="M9 17l3 3 3-3"/>',
  edit: '<path d="M4.5 19.5l1-4.2L15.8 5a1.8 1.8 0 0 1 2.6 0l.6.6a1.8 1.8 0 0 1 0 2.6L8.7 18.5z"/><path d="M13.5 7.3l3.2 3.2"/>',
  tag: '<path d="M3.5 12.3V4.8c0-.7.6-1.3 1.3-1.3h7.5l8.2 8.2c.5.5.5 1.3 0 1.8l-6.7 6.7c-.5.5-1.3.5-1.8 0z"/><circle cx="8" cy="8" r="1.4"/>',
  arrowR: '<path d="M5 12h13.5M13 6.5l5.5 5.5-5.5 5.5"/>',
  plus: '<path d="M12 6v12M6 12h12"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  offline: '<path d="M4 4l16 16"/><path d="M8.5 8.6A6.5 6.5 0 0 0 5.6 11.8"/><path d="M12 7.5c2.8 0 5.2 1.6 6.4 4"/><path d="M9.7 14.4a3.2 3.2 0 0 1 4.6 0"/><path d="M12 18h.01"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><path d="M12 7.6h.01"/>',
  grid: '<rect x="4" y="4" width="6.6" height="6.6" rx="1.7"/><rect x="13.4" y="4" width="6.6" height="6.6" rx="1.7"/><rect x="4" y="13.4" width="6.6" height="6.6" rx="1.7"/><rect x="13.4" y="13.4" width="6.6" height="6.6" rx="1.7"/>',
  ring: '<circle class="tr" cx="12" cy="12" r="9"/><circle class="pr" cx="12" cy="12" r="9"/>',
  c1: '<path d="M12 14.5V4.5"/><path d="M7.8 8.7L12 4.5l4.2 4.2"/><path d="M5 13v4.3A2.7 2.7 0 0 0 7.7 20h8.6a2.7 2.7 0 0 0 2.7-2.7V13"/>',
  c2: '<path d="M4 8.8a2.2 2.2 0 0 1 2.2-2.2h1.7l1.4-2.1h5.4l1.4 2.1h1.7A2.2 2.2 0 0 1 20 8.8v8a2.2 2.2 0 0 1-2.2 2.2H6.2A2.2 2.2 0 0 1 4 16.8z"/><circle cx="12" cy="12.6" r="3.3"/>',
  c5: '<path d="M12 4l8.5 4.4L12 12.8 3.5 8.4z"/><path d="M3.5 12.4L12 16.8l8.5-4.4"/><path d="M3.5 16.1L12 20.5l8.5-4.4"/>',
};
function ic(name, cls) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"${cls ? ` class="${cls}"` : ''} aria-hidden="true">${P[name] || ''}</svg>`;
}
const ringSvg = () => `<svg viewBox="0 0 24 24" aria-hidden="true">${P.ring}</svg>`;
const PCAT_ICON = { 1: 'c1', 2: 'c2', 4: 'film', 5: 'c5' };

/* ═════════════════════════════════════════════ 모듈 캐시(다시 붙여도 유지) */
const POSTER = new Map();   // path -> {s:'ready'|'failed'|'none', url}
const INFO = new Map();     // path -> {duration, width, height, playable}
const PROXY = new Map();    // path -> {s:'ready'|'failed', url}
function noteInfo(path, r) {
  if (!path || !r) return;
  const o = INFO.get(path) || {};
  for (const k of ['duration', 'width', 'height', 'playable']) if (r[k] != null && r[k] !== '') o[k] = r[k];
  INFO.set(path, o);
}
const durOf = m => { if (!m) return 0; const i = INFO.get(m.path); return (i && i.duration) || m.duration || 0; };
const playOf = m => { if (!m) return 'none'; const i = INFO.get(m.path); return (i && i.playable) || m.playable || 'none'; };

/* ═════════════════════════════════════════════ 인스턴스 */
function create(container, ctx) {
  ctx = ctx || {};
  const TOKEN = (document.querySelector('meta[name="db-token"]') || {}).content || '';
  const disposers = [];
  const on = (t, ev, fn, o) => { t.addEventListener(ev, fn, o); disposers.push(() => t.removeEventListener(ev, fn, o)); };
  let alive = true;

  /* ── ctx 기본값(셸이 안 주면 스스로) */
  async function api(path, opts = {}) {
    if (typeof ctx.api === 'function') return ctx.api(path, opts);
    const o = { method: opts.method || 'GET', headers: { 'X-DB-Token': TOKEN, Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' };
    if (opts.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
    const res = await fetch(path, o);
    let d = null;
    try { d = await res.json(); } catch (e) { d = null; }
    if (!res.ok || !d || d.ok === false) { const e = new Error((d && d.message) || '요청을 처리하지 못했어요.'); e.status = res.status; e.data = d || {}; e.code = (d && d.error) || 'error'; throw e; }
    return d;
  }
  const GET = (p, q) => {
    if (q) { const qs = new URLSearchParams(); for (const k in q) if (q[k] !== undefined && q[k] !== null && q[k] !== '') qs.set(k, q[k]); const s = qs.toString(); if (s) p += '?' + s; }
    return api(p, { method: 'GET' });
  };
  const POST = (p, b) => api(p, { method: 'POST', body: b || {} });
  const errCode = e => (e && (e.code || (e.data && e.data.error))) || 'error';
  const fmtSize = n => {
    if (typeof ctx.fmtSize === 'function') return ctx.fmtSize(n);
    n = Number(n) || 0;
    const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let i = 0;
    while (n >= 1000 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? String(Math.round(n)) : n >= 100 ? Math.floor(n) : n >= 10 ? (Math.floor(n * 10) / 10) : (Math.floor(n * 100) / 100)) + u[i];
  };
  const fmtAgo = ts => (typeof ctx.fmtAgo === 'function' ? ctx.fmtAgo(ts) : fmtTs(ts));
  const toast = (msg, o) => {
    if (typeof ctx.toast === 'function') return ctx.toast(msg, o || {});
    return null;
  };
  const toastErr = (e, title) => toast(title || (e && e.message) || '요청을 처리하지 못했어요.', { kind: 'err', sub: title ? (e && e.message) : null });
  const catName = i => { const c = (ctx.cats || [])[i]; return (c && c.name) || ['캐시·임시', '렌더·출력', '촬영·소스', '프록시', '기타 영상', '프로젝트', '이미지·시퀀스', '오디오', '압축·백업', '앱·시스템', '기타'][i] || '기타'; };
  const catVar = i => `var(--c${i})`;
  const drivesNow = () => { try { return (typeof ctx.drives === 'function' ? ctx.drives() : ctx.drives) || []; } catch (e) { return []; } };
  const driveByKey = L => drivesNow().find(d => d.letter === L) || null;

  /* ── 상태 */
  const S = {
    group: store.get('group', 'client'), q: '', drive: '*', worker: '', period: 'all', sort: store.get('sort', 'recent'),
    year: null, data: null, seq: 0, loading: false,
    collapsed: new Set(store.get('collapsed', [])),
    sel: new Map(), anchor: null, order: [],
    cards: new Map(), lastSig: '', jobSig: '', settingsCache: null, lastState: 0,
  };
  if (!['client', 'time', 'month'].includes(S.group)) S.group = 'client';
  if (!['recent', 'size', 'name'].includes(S.sort)) S.sort = 'recent';

  /* ── 뼈대 */
  const root = h('div', { class: 'pj', 'data-group': S.group });
  container.append(root);
  const scrollEl = () => scrollParent(root);
  /* 여백(gutter): 셸이 .screen 으로 감싸면 그 여백을, 아니면 스스로 32px. 가로 줄·스티키 띠가 스크롤 영역 끝까지 번진다. */
  let padMode = null;
  function measure() {
    if (!alive || !root.isConnected) return;
    const sc = scrollEl();
    if (!sc || sc === document.scrollingElement || sc === document.documentElement) { if (padMode !== true) { padMode = true; root.classList.add('pj-pad'); } return; }
    let x = 0;
    for (let e = root; e && e !== sc; e = e.offsetParent) { x += e.offsetLeft; if (e.offsetParent && !sc.contains(e.offsetParent)) break; }
    if (padMode === null) { padMode = x < 8; root.classList.toggle('pj-pad', padMode); if (padMode) return; }
    if (padMode) return;
    const right = sc.clientWidth - x - root.offsetWidth;
    const gx = Math.max(12, Math.round(Math.min(x, right)));
    root.style.setProperty('--pj-gx', gx + 'px');
  }
  requestAnimationFrame(measure);
  setTimeout(measure, 320);

  const head = h('div', { class: 'pj-head' });
  const title = h('div', {}, h('h1', { text: '프로젝트' }), h('div', { class: 'sub num', id: 'pjSub' }));
  const btnOrg = h('button', { class: 'btn sm ghost', type: 'button', html: ic('tag') + '<span>광고주 정리</span>', 'data-tip': '광고주 합치기·이름 바꾸기, 작업자 이름, 프로젝트 폴더' });
  head.append(title, h('div', { class: 'sp' }), h('div', { class: 'acts' }, btnOrg));
  const subEl = title.querySelector('.sub');

  const SORTS = [{ id: 'recent', label: '최신순' }, { id: 'size', label: '크기순' }, { id: 'name', label: '이름순' }];
  const bar = h('div', { class: 'pj-bar' });
  const GROUPS = [{ id: 'client', label: '광고주별' }, { id: 'time', label: '시간순' }, { id: 'month', label: '연·월별' }];
  const tabs = Seg(GROUPS, S.group, id => setGroup(id), 'pj-tabs');
  const search = h('div', { class: 'search pj-q' });
  search.innerHTML = ic('search') + '<input type="text" autocomplete="off" spellcheck="false" placeholder="프로젝트·광고주 검색" aria-label="프로젝트 검색"><button class="clr" type="button" aria-label="검색어 지우기">' + ic('x') + '</button>';
  const qIn = search.querySelector('input');
  const ddDrive = Dropdown({ icon: 'drive', label: '드라이브', value: () => S.drive, options: driveOptions, onPick: v => { S.drive = v; load(); } });
  const ddWorker = Dropdown({ icon: 'people', label: '작업자', value: () => S.worker, options: workerOptions, onPick: v => { S.worker = v; load(); } });
  const ddPeriod = Dropdown({ icon: 'cal', label: '기간', value: () => S.period, options: periodOptions, onPick: v => { S.period = v; load(); } });
  const ddSort = Dropdown({ icon: 'sort', label: '정렬', value: () => S.sort, options: () => SORTS, onPick: v => { S.sort = v; store.set('sort', v); load(); }, keepOn: true });
  const resetBtn = h('button', { class: 'btn sm ghost pj-reset', type: 'button', text: '초기화', hidden: true, 'data-tip': '필터를 모두 풀어요' });
  bar.append(tabs.el, search, h('div', { class: 'sp' }), ddDrive.el, ddWorker.el, ddPeriod.el, ddSort.el, resetBtn);

  const loadBar = h('div', { class: 'pj-load' });
  const body = h('div', { class: 'pj-body' });
  root.append(head, bar, loadBar, body);

  function driveOptions() {
    const ds = drivesNow().filter(d => d.scanned || (S.data && S.data._drives && S.data._drives.has(d.letter)));
    const counts = S.allCounts || {};
    return [{ id: '*', label: '모든 드라이브' }, null].concat(ds.map(d => ({
      id: d.letter, label: driveName(d), sub: counts[d.letter] != null ? counts[d.letter] + '개' : (d.connected === false ? '연결 안 됨' : ''),
    })));
  }
  function workerOptions() {
    const ws = (S.data && S.data.workers) || [];
    const opts = [{ id: '', label: '모든 작업자' }];
    if (ws.length) opts.push(null);
    for (const w of ws) opts.push({ id: w.initial, label: w.name || w.initial, ini: w.name ? w.initial : '', sub: w.count + '개' });
    if (S.worker && !ws.some(w => w.initial === S.worker)) opts.push({ id: S.worker, label: S.worker });
    return opts;
  }
  function periodOptions() {
    const ys = ((S.data && S.data.years) || []).map(y => y.year);
    const opts = [{ id: 'all', label: '전체 기간' }, null, { id: 'd90', label: '최근 3개월' }, { id: 'd365', label: '최근 1년' }];
    if (ys.length) opts.push(null);
    for (const y of ys) opts.push({ id: 'y' + y, label: y + '년' });
    if (/^y\d{4}$/.test(S.period) && !ys.includes(+S.period.slice(1))) opts.push({ id: S.period, label: S.period.slice(1) + '년' });
    return opts;
  }
  function driveName(d) {
    if (!d) return '';
    if (d.kind === 'network' || String(d.letter).startsWith('N-')) return d.label || 'NAS';
    if (String(d.letter).startsWith('~')) return (d.label || '외장') + ' (연결 안 됨)';
    return `${d.label || '로컬 디스크'} (${d.letter}:)`;
  }

  /* ── 세그먼트(움직이는 표시) */
  function Seg(items, cur, onChange, cls) {
    const el = h('div', { class: 'tabs ' + (cls || ''), role: 'tablist' });
    const ind = h('span', { class: 'ind' });
    el.append(ind);
    let curId = cur, placed = false;
    const btns = items.map(it => {
      const b = h('button', { type: 'button', role: 'tab', class: it.id === cur ? 'on' : '', 'aria-selected': it.id === cur ? 'true' : 'false', text: it.label, 'data-id': it.id });
      b.addEventListener('click', () => { if (curId !== it.id) { set(it.id); onChange(it.id); } });
      el.append(b);
      return b;
    });
    function place(anim) {
      const b = btns[items.findIndex(x => x.id === curId)];
      if (!b || !b.offsetWidth) return;
      if (!anim || reduced) ind.style.transition = 'none';
      ind.style.width = b.offsetWidth + 'px';
      ind.style.transform = `translateX(${b.offsetLeft}px)`;
      if (!anim || reduced) { void ind.offsetWidth; ind.style.transition = ''; }
      placed = true;
    }
    function set(id) {
      curId = id;
      btns.forEach((b, i) => { b.classList.toggle('on', items[i].id === id); b.setAttribute('aria-selected', items[i].id === id ? 'true' : 'false'); });
      place(placed);
    }
    if (window.ResizeObserver) { const ro = new ResizeObserver(() => place(false)); ro.observe(el); disposers.push(() => ro.disconnect()); }
    requestAnimationFrame(() => place(false));
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => place(false));
    return { el, set, get value() { return curId; } };
  }

  /* ── 드롭다운(.pop .menu 재사용, 네이티브 select 금지) */
  let openPop = null;
  function closePop(now) {
    const p = openPop;
    if (!p) return;
    openPop = null;
    p.btn && p.btn.classList.remove('open');
    p.cleanup();
    if (now || reduced) { p.el.remove(); return; }
    p.el.classList.add('closing');
    setTimeout(() => p.el.remove(), 160);
  }
  function popAt(btn, content, o = {}) {
    closePop(true);
    const el = h('div', { class: 'pop ' + (o.cls || '') });
    el.append(content);
    document.body.append(el);
    const r = btn.getBoundingClientRect();
    const w = el.offsetWidth, hh = el.offsetHeight;
    let x = o.alignRight ? r.right - w : r.left;
    x = clamp(x, 8, innerWidth - w - 8);
    let y = r.bottom + 6;
    let oy = '0%';
    if (y + hh > innerHeight - 8) { y = Math.max(8, r.top - hh - 6); oy = '100%'; }
    el.style.left = Math.round(x) + 'px';
    el.style.top = Math.round(y) + 'px';
    el.style.zIndex = '520';
    el.style.setProperty('--ox', Math.round(r.left + r.width / 2 - x) + 'px');
    el.style.setProperty('--oy', oy);
    btn.classList.add('open');
    const down = e => { if (!el.contains(e.target) && !btn.contains(e.target)) closePop(); };
    const key = e => {
      if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); closePop(); btn.focus && btn.focus(); return; }
      if (o.onKey) o.onKey(e);
    };
    const scr = e => { if (!el.contains(e.target)) closePop(); };
    document.addEventListener('pointerdown', down, true);
    document.addEventListener('keydown', key, true);
    document.addEventListener('scroll', scr, true);
    window.addEventListener('resize', scr);
    openPop = { el, btn, cleanup() { document.removeEventListener('pointerdown', down, true); document.removeEventListener('keydown', key, true); document.removeEventListener('scroll', scr, true); window.removeEventListener('resize', scr); } };
    requestAnimationFrame(() => el.classList.add('show'));
    return el;
  }
  disposers.push(() => closePop(true));

  function Dropdown(o) {
    const el = h('button', { class: 'dd', type: 'button', 'aria-haspopup': 'menu', 'data-tip': o.label, 'aria-label': o.label });
    function label() {
      const v = o.value();
      const opt = o.options().find(x => x && x.id === v);
      const isDef = !o.keepOn && (o.options()[0] || {}).id === v;
      el.classList.toggle('on', !isDef);
      el.innerHTML = ic(o.icon) + (isDef ? `<span class="lb">${esc(o.label)}</span>` : `<b>${esc(opt ? opt.label : v)}</b>`) + ic('chevD', 'cv');
      if (o.keepOn) el.classList.remove('on');
    }
    el.addEventListener('click', () => {
      if (openPop && openPop.btn === el) { closePop(); return; }
      const v = o.value();
      const box = h('div', { class: 'menu pj-menu', role: 'menu' });
      const items = [];
      for (const it of o.options()) {
        if (!it) { box.append(h('div', { class: 'msep' })); continue; }
        const b = h('button', { class: 'mi', type: 'button', role: 'menuitemradio', 'aria-checked': it.id === v ? 'true' : 'false' });
        b.innerHTML = ic('check', 'chk' + (it.id === v ? '' : ' no')) + `<span class="lbl">${esc(it.label)}</span>` + (it.ini ? `<span class="ini">${esc(it.ini)}</span>` : '') + (it.sub ? `<span class="sub">${esc(it.sub)}</span>` : '');
        b.addEventListener('click', () => { closePop(); if (it.id !== o.value()) { o.onPick(it.id); label(); } });
        items.push(b);
        box.append(b);
      }
      let fi = Math.max(0, items.findIndex(b => b.getAttribute('aria-checked') === 'true'));
      popAt(el, box, {
        onKey(e) {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault(); e.stopPropagation();
            fi = clamp(fi + (e.key === 'ArrowDown' ? 1 : -1), 0, items.length - 1);
            items[fi].focus();
          }
        },
      });
      requestAnimationFrame(() => items[fi] && items[fi].focus({ preventScroll: true }));
    });
    label();
    return { el, refresh: label };
  }

  /* ═════════════════════════════════════════ 미리보기: 포스터 */
  const Posters = (() => {
    const subs = new Map();   // path -> Set(cb)
    const media = new Map();  // path -> media
    const stack = [];         // LIFO — 화면에 막 들어온 것부터
    const waiting = new Map(); // path -> {t0, last}
    let inflight = 0, timer = 0, noFF = false;
    const capQ = [];
    let capBusy = 0;

    function notify(path, v) {
      POSTER.set(path, v);
      waiting.delete(path);
      const set = subs.get(path);
      if (set) { subs.delete(path); set.forEach(cb => { try { cb(v); } catch (e) { /* 무시 */ } }); }
    }
    function want(m, cb) {
      if (!m || !m.path) { cb({ s: 'none' }); return; }
      const k = m.path;
      const c = POSTER.get(k);
      if (c) { cb(c); return; }
      if (m.poster_url) { const v = { s: 'ready', url: m.poster_url }; POSTER.set(k, v); cb(v); return; }
      if (m.offline || (!m.video_url && !/^(jpe?g|png|webp|gif)$/i.test(m.ext || ''))) { cb({ s: 'none' }); return; }
      media.set(k, m);
      let set = subs.get(k);
      if (!set) { set = new Set(); subs.set(k, set); }
      set.add(cb);
      if (!waiting.has(k)) { const i = stack.indexOf(k); if (i >= 0) stack.splice(i, 1); stack.push(k); }
      pump();
    }
    function unwant(path, cb) {
      const set = subs.get(path);
      if (!set) return;
      set.delete(cb);
      if (!set.size) { subs.delete(path); const i = stack.indexOf(path); if (i >= 0) stack.splice(i, 1); }
    }
    function pump() {
      while (alive && inflight < 3 && stack.length) {
        const k = stack.pop();
        if (!subs.has(k)) continue;
        req(k);
      }
    }
    async function req(k) {
      const m = media.get(k);
      if (!m) return;
      if (noFF) { capture(k); return; }
      inflight++;
      try {
        const r = await POST('/api/media/poster', { path: k });
        noteInfo(k, r);
        if (r.status === 'ready' && r.url) notify(k, { s: 'ready', url: r.url });
        else if (r.status === 'queued') { const w = waiting.get(k) || { t0: performance.now() }; w.last = performance.now(); waiting.set(k, w); arm(); }
        else if (r.status === 'no_ffmpeg') { noFF = true; capture(k); }
        else notify(k, { s: 'failed' });
        touchInfo(k);
      } catch (e) {
        // 잠깐의 오류는 캐시하지 않는다 — 다음에 다시 보이면 또 묻는다
        const set = subs.get(k);
        subs.delete(k);
        if (set) set.forEach(cb => { try { cb({ s: 'failed', soft: true }); } catch (er) { /* 무시 */ } });
      } finally { inflight--; pump(); }
    }
    function arm() { if (!timer) timer = setInterval(tick, 1200); }
    function tick() {
      if (!alive) return;
      const now = performance.now();
      if (!waiting.size) { clearInterval(timer); timer = 0; return; }
      let n = 0;
      for (const [k, w] of [...waiting.entries()].reverse()) {
        if (now - w.t0 > 90000) { notify(k, { s: 'failed' }); continue; }
        if (!subs.has(k)) { waiting.delete(k); continue; }
        if (now - w.last < 1000 || n >= 4) continue;
        n++;
        w.last = now;
        waiting.delete(k);
        stack.push(k);
      }
      pump();
    }
    function onDone(list) {
      let hit = false;
      for (const e of list || []) {
        if (e && e.kind === 'poster' && waiting.has(e.path) && subs.has(e.path)) { waiting.delete(e.path); stack.push(e.path); hit = true; }
      }
      if (hit) pump();
    }
    /* ffmpeg 가 없을 때: 브라우저가 1초 지점 프레임을 캔버스로 */
    function capture(k) {
      const m = media.get(k);
      if (!m || !m.video_url || playOf(m) !== 'direct') { notify(k, { s: 'failed' }); return; }
      capQ.push(k);
      capPump();
    }
    function capPump() {
      while (capBusy < 2 && capQ.length) {
        const k = capQ.pop();
        if (!subs.has(k)) continue;
        capBusy++;
        grab(media.get(k)).then(url => notify(k, url ? { s: 'ready', url, client: true } : { s: 'failed' }))
          .finally(() => { capBusy--; capPump(); });
      }
    }
    function grab(m) {
      return new Promise(res => {
        const v = document.createElement('video');
        v.muted = true; v.preload = 'auto'; v.playsInline = true;
        let done = false;
        const fin = url => { if (done) return; done = true; clearTimeout(to); v.removeAttribute('src'); try { v.load(); } catch (e) { /* 무시 */ } res(url); };
        const to = setTimeout(() => fin(null), 9000);
        v.addEventListener('loadedmetadata', () => {
          noteInfo(m.path, { duration: v.duration, width: v.videoWidth, height: v.videoHeight });
          try { v.currentTime = Math.min(1, (v.duration || 2) * 0.1); } catch (e) { fin(null); }
        });
        v.addEventListener('seeked', () => {
          try {
            const w = 480, hh = Math.round(w * (v.videoHeight || 9) / (v.videoWidth || 16));
            const c = document.createElement('canvas');
            c.width = w; c.height = hh;
            c.getContext('2d').drawImage(v, 0, 0, w, hh);
            fin(c.toDataURL('image/jpeg', 0.82));
          } catch (e) { fin(null); }
        });
        v.addEventListener('error', () => fin(null));
        v.src = m.video_url;
      });
    }
    disposers.push(() => { clearInterval(timer); timer = 0; });
    return { want, unwant, onDone, get noFF() { return noFF; } };
  })();
  function touchInfo(path) { for (const c of S.cards.values()) if (c.m && c.m.path === path) c.syncDur(); }

  /* ═════════════════════════════════════════ 미리보기: 재생용 프록시 */
  function getProxy(m, onQueued, signal) {
    return new Promise(async res => {
      const k = m.path;
      const c = PROXY.get(k);
      if (c) { res(c.s === 'ready' ? c.url : null); return; }
      const t0 = performance.now();
      let said = false;
      while (alive && !(signal && signal.stop)) {
        let r;
        try { r = await POST('/api/media/proxy', { path: k }); } catch (e) { res(null); return; }
        noteInfo(k, r);
        if (r.status === 'ready' && r.url) { PROXY.set(k, { s: 'ready', url: r.url }); res(r.url); return; }
        if (r.status !== 'queued') { PROXY.set(k, { s: 'failed' }); res(null); return; }
        if (!said) { said = true; onQueued && onQueued(); }
        if (performance.now() - t0 > 180000) { res(null); return; }
        await waitDone(k, 'proxy', 1000);
      }
      res(null);
    });
  }
  const doneWaiters = new Set();
  function waitDone(path, kind, ms) {
    return new Promise(r => {
      const w = { path, kind, r };
      doneWaiters.add(w);
      setTimeout(() => { if (doneWaiters.delete(w)) r(); }, ms);
    });
  }
  function fireDone(list) {
    for (const e of list || []) for (const w of [...doneWaiters]) if (w.path === e.path && w.kind === e.kind) { doneWaiters.delete(w); w.r(); }
  }

  /* ═════════════════════════════════════════ 카드 호버 미리보기(한 번에 하나) */
  const Hover = (() => {
    const v = document.createElement('video');
    v.muted = true; v.loop = true; v.playsInline = true; v.preload = 'auto';
    v.setAttribute('muted', ''); v.setAttribute('playsinline', ''); v.setAttribute('disablepictureinpicture', '');
    let cur = null, timer = 0, tok = 0, rafId = 0, triedProxy = false, stopTimer = 0;
    v.addEventListener('playing', () => { if (cur) cur.el.classList.add('playing'); });
    v.addEventListener('loadedmetadata', () => {
      if (!cur) return;
      noteInfo(cur.m.path, { duration: v.duration, width: v.videoWidth, height: v.videoHeight });
      cur.syncDur();
    });
    v.addEventListener('error', () => {
      if (!cur || !v.getAttribute('src')) return;
      const c = cur, my = tok;
      if (!triedProxy && c.m && c.m.video_url) { triedProxy = true; viaProxy(c, my); return; }
      c.el.classList.remove('queued', 'playing');
      flashNote(c, '재생할 수 없는 영상이에요');
    });
    function flashNote(c, t) {
      const n = c.el.querySelector('.po-note');
      if (!n || c.el.classList.contains('pvfail') || c.el.classList.contains('nopv')) return;
      n.textContent = t;
      c.el.classList.add('nop');
      setTimeout(() => c.el.classList.remove('nop'), 1600);
    }
    function loop() {
      rafId = 0;
      if (!cur) return;
      const d = v.duration || durOf(cur.m);
      if (d && !cur.el.classList.contains('scrub')) cur.fill.style.width = (v.currentTime / d * 100).toFixed(2) + '%';
      rafId = requestAnimationFrame(loop);
    }
    function intent(c) {
      if (cur === c) return;
      clearTimeout(timer);
      timer = setTimeout(() => start(c), 250);
    }
    function cancel(c) {
      clearTimeout(timer);
      if (cur === c) stop();
    }
    async function start(c) {
      if (!alive || !c.el.isConnected) return;
      stop(true);
      const m = c.m;
      if (!m || m.offline || !c.p.connected) return;
      if (c.posterState === 'failed' && !PROXY.has(m.path)) return;   // 서버가 프레임 하나도 못 읽은 파일 — 재생도 안 된다
      const pl = playOf(m);
      if (pl === 'none' && !m.video_url) return;
      cur = c; tok++; triedProxy = false;
      const my = tok;
      c.vidBox.append(v);
      if (pl === 'proxy' || !m.video_url) { triedProxy = true; viaProxy(c, my); return; }
      const px = PROXY.get(m.path);
      play(px && px.s === 'ready' ? px.url : m.video_url, my);
    }
    async function viaProxy(c, my) {
      if (PROXY.has(c.m.path) && PROXY.get(c.m.path).s === 'failed') { flashNote(c, '미리보기를 만들 수 없어요'); return; }
      const sig = { stop: false };
      c._sig = sig;
      const url = await getProxy(c.m, () => { if (tok === my) c.el.classList.add('queued'); }, sig);
      c.el.classList.remove('queued');
      if (tok !== my || cur !== c) return;
      if (!url) { flashNote(c, '미리보기를 만들 수 없어요'); return; }
      play(url, my);
    }
    function play(url, my) {
      if (tok !== my || !cur) return;
      if (v.getAttribute('src') !== url) { v.src = url; }
      try { v.currentTime = 0; } catch (e) { /* 무시 */ }
      if (reduced) return;   // 움직임 줄이기: 저절로 재생하지 않고, 가로로 움직일 때만 스크럽
      const p = v.play();
      if (p && p.catch) p.catch(() => { /* 정지·교체로 인한 중단은 무시 */ });
      if (!rafId) rafId = requestAnimationFrame(loop);
    }
    function stop(immediate) {
      clearTimeout(timer);
      const c = cur;
      cur = null; tok++;
      if (rafId) cancelAnimationFrame(rafId);
      rafId = 0;
      clearTimeout(stopTimer);
      if (!c) return;
      if (c._sig) c._sig.stop = true;
      c.el.classList.remove('playing', 'queued', 'scrub');
      c.fill.style.width = '0%';
      const fin = () => { if (cur) return; try { v.pause(); } catch (e) { /* 무시 */ } v.removeAttribute('src'); try { v.load(); } catch (e) { /* 무시 */ } if (v.parentNode) v.remove(); };
      if (immediate || reduced) fin(); else stopTimer = setTimeout(fin, 330);
    }
    function scrub(c, x) {
      if (cur !== c || !v.getAttribute('src')) return;
      const d = v.duration;
      if (!d || !isFinite(d)) return;
      const r = c.po.getBoundingClientRect();
      const f = clamp((x - r.left) / r.width, 0, 1);
      c.el.classList.add('scrub');
      if (!v.paused) v.pause();
      try { v.currentTime = f * d; } catch (e) { /* 무시 */ }
      c.fill.style.width = (f * 100).toFixed(2) + '%';
      c.tm.textContent = fmtTime(f * d);
      c.tm.style.left = clamp(f * r.width, 22, r.width - 22) + 'px';
      c.el.classList.add('playing');
    }
    disposers.push(() => stop(true));
    return { intent, cancel, stop, scrub, get cur() { return cur; }, video: v };
  })();

  /* ═════════════════════════════════════════ 카드 */
  function itemOf(p) {
    return {
      key: `${p.drive}:dir:${p.did}`, drive: p.drive, kind: 'dir', id: p.did, name: p.name, path: p.path,
      size: p.size, metric: p.size, cat: dominantCat(p.cat_bytes), cat_bytes: p.cat_bytes, files: p.files, mtime: p.newest,
      protected: !p.connected, protected_reason: p.connected ? '' : '연결되지 않은 장치라 다룰 수 없어요',
      project: true,
    };
  }
  function dominantCat(cb) { let b = 10, v = -1; (cb || []).forEach((x, i) => { if (x > v) { v = x; b = i; } }); return b; }
  function miniBar(cb, total, cls) {
    const el = h('div', { class: 'pj-mb ' + (cls || '') });
    const t = total || (cb || []).reduce((a, b) => a + (b || 0), 0);
    if (!t) return el;
    const order = (cb || []).map((v, i) => [i, v || 0]).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]);
    for (const [i, v] of order) {
      const pc = v / t * 100;
      if (pc < 0.4) continue;
      el.append(h('b', { style: { width: pc.toFixed(2) + '%', '--sc': catVar(i) } }));
    }
    return el;
  }
  function compTip(cb, total) {
    const t = total || (cb || []).reduce((a, b) => a + (b || 0), 0);
    if (!t) return '';
    return (cb || []).map((v, i) => [i, v || 0]).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]).slice(0, 4)
      .map(([i, v]) => `${catName(i)} ${fmtSize(v)} (${Math.round(v / t * 100)}%)`).join('\n');
  }
  const workerText = p => p.worker_name ? p.worker_name : (p.worker || '');
  const cardSig = p => [p.title, p.name, p.client, p.date, p.size, p.worker, p.worker_name, p.connected, p.preview && p.preview.path, p.preview && p.preview.video_url, (p.cat_bytes || []).join(',')].join('|');

  const io = 'IntersectionObserver' in window ? new IntersectionObserver(ents => {
    for (const e of ents) { const c = e.target._pc; if (!c) continue; if (e.isIntersecting) c.show(); else c.hide(); }
  }, { root: null, rootMargin: '400px 300px' }) : null;
  disposers.push(() => io && io.disconnect());

  function Card(p) {
    const el = h('article', { class: 'pc', tabindex: '0', 'data-k': p.key, 'aria-label': p.title || p.name });
    const po = h('div', { class: 'pc-po' });
    const fb = h('div', { class: 'po-fb' });
    const img = h('img', { class: 'po-img', alt: '', decoding: 'async', draggable: 'false' });
    const vidBox = h('div', { class: 'po-vid' });
    const client = h('span', { class: 'po-client' });
    const dur = h('span', { class: 'po-dur' });
    const ck = h('button', { class: 'ck pc-ck', type: 'button', html: ic('check'), 'aria-label': '선택', tabindex: '-1' });
    const ring = h('div', { class: 'po-ring', html: ringSvg() });
    const scrubEl = h('div', { class: 'po-scrub' }, h('i'));
    const tm = h('span', { class: 'po-tm' });
    const note = h('span', { class: 'po-note' });
    po.append(fb, img, vidBox, h('div', { class: 'po-shade' }), client, dur, ck, ring, scrubEl, tm, note);
    const tEl = h('div', { class: 'pc-t' });
    const mEl = h('div', { class: 'pc-m' });
    const sV = h('span', { class: 'v num' });
    const sBar = h('div', { class: 'pc-bw' });
    const info = h('div', { class: 'pc-info' }, tEl, mEl, h('div', { class: 'pc-s' }, sV, sBar));
    el.append(po, info);
    const c = { el, po, img, vidBox, fill: scrubEl.firstChild, tm, p, m: null, sig: '', vis: false, pcb: null };
    el._pc = c;

    c.update = np => {
      c.p = np;
      const sig = cardSig(np);
      if (sig === c.sig) return;
      c.sig = sig;
      const m = np.preview || null;
      const pathChanged = (c.m && c.m.path) !== (m && m.path);
      c.m = m;
      el.classList.toggle('off', !np.connected);
      el.style.setProperty('--pc-tint', catVar(dominantCat(np.cat_bytes)));
      fb.innerHTML = ic(!m ? 'folder' : (!pathChanged && el.classList.contains('pvfail')) ? 'nofilm' : 'film') + `<span>${m ? '미리보기를 만들 수 없어요' : '영상이 없어요'}</span>`;
      const unk = !np.client || np.client === '미분류';
      client.textContent = np.client || '미분류';
      client.style.opacity = unk ? '.62' : '';
      tEl.textContent = np.title || np.name;
      tEl.setAttribute('data-tip', np.name);
      const w = workerText(np);
      mEl.innerHTML = `<span>${esc(fmtYMD(np.date))}</span>` + (w ? `<i>·</i><span>${esc(w)}</span>` : '') + (S.drive === '*' ? `<i>·</i><span>${esc(shortDrive(np))}</span>` : '');
      sV.textContent = fmtSize(np.size);
      sBar.replaceChildren(miniBar(np.cat_bytes, np.size, 'sm'));
      sBar.setAttribute('data-tip', compTip(np.cat_bytes, np.size));
      let off = po.querySelector('.po-off');
      if (!np.connected) {
        if (!off) { off = h('span', { class: 'po-off', html: ic('offline') + '<span>연결 안 됨</span>' }); po.append(off); }
      } else if (off) off.remove();
      el.classList.toggle('nopv', !m);
      c.syncDur();
      if (pathChanged) {
        img.classList.remove('on');
        img.removeAttribute('src');
        el.classList.remove('pvfail');
        c.posterState = null;
        if (c.vis) { c.hide(); c.show(); }
      }
      el.classList.toggle('sel', S.sel.has(itemKey(np)));
    };
    c.syncDur = () => { const d = durOf(c.m); dur.textContent = d ? fmtTime(d) : ''; };
    c.show = () => {
      c.vis = true;
      if (!c.m || c.posterState === 'ready' || c.pcb) return;
      const cached = POSTER.get(c.m.path);
      if (!cached) el.classList.add('loading');
      const cb = v => {
        c.pcb = null;
        el.classList.remove('loading');
        if (v.s === 'ready') {
          c.posterState = 'ready';
          if (img.getAttribute('src') !== v.url) {
            img.onload = () => img.classList.add('on');
            img.onerror = () => { img.classList.remove('on'); el.classList.add('pvfail'); };
            img.src = v.url;
            if (img.complete && img.naturalWidth) img.classList.add('on');
          }
        } else if (!v.soft) {
          c.posterState = v.s;
          el.classList.add('pvfail');
          const sv = fb.querySelector('svg');
          if (sv) sv.outerHTML = ic('nofilm');
        }
      };
      c.pcb = cb;
      Posters.want(c.m, cb);
    };
    c.hide = () => {
      c.vis = false;
      if (c.pcb && c.m) { Posters.unwant(c.m.path, c.pcb); c.pcb = null; el.classList.remove('loading'); }
    };

    // 상호작용
    let lx = null, moved = 0;
    po.addEventListener('pointerenter', e => { lx = e.clientX; moved = 0; if (e.pointerType !== 'touch') Hover.intent(c); });
    po.addEventListener('pointermove', e => {
      if (lx == null) { lx = e.clientX; return; }
      moved += Math.abs(e.clientX - lx);
      lx = e.clientX;
      if (moved > 18 && Hover.cur === c) Hover.scrub(c, e.clientX);
    });
    po.addEventListener('pointerleave', () => { lx = null; moved = 0; Hover.cancel(c); });
    ck.addEventListener('click', e => { e.stopPropagation(); toggleSel(c.p, e.shiftKey); });
    el.addEventListener('click', e => {
      if (e.target.closest('.pc-ck')) return;
      if (e.ctrlKey || e.metaKey) { e.preventDefault(); toggleSel(c.p, false); return; }
      if (e.shiftKey && S.anchor) { e.preventDefault(); rangeSel(c.p); return; }
      if (S.sel.size) { toggleSel(c.p, false); return; }
      openProject(c.p, c);
    });
    el.addEventListener('keydown', e => {
      if (e.target !== el) return;
      if (e.key === 'Enter') { e.preventDefault(); openProject(c.p, c); }
      else if (e.key === ' ') { e.preventDefault(); toggleSel(c.p, e.shiftKey); }
    });
    c.update(p);
    if (io) io.observe(el); else c.show();
    return c;
  }
  function shortDrive(p) {
    const d = driveByKey(p.drive);
    if (p.drive_kind === 'network' || String(p.drive).startsWith('N-')) return (d && d.label) || p.drive_label || 'NAS';
    if (String(p.drive).startsWith('~')) return (p.drive_label || '외장').replace(/\s*\([^)]*\)\s*$/, '');
    return p.drive + ':';
  }
  function cardFor(p) {
    let c = S.cards.get(p.key);
    if (c) { c.update(p); return c; }
    c = Card(p);
    S.cards.set(p.key, c);
    return c;
  }
  function gcCards(keep) {
    for (const [k, c] of S.cards) {
      if (keep.has(k)) continue;
      if (Hover.cur === c) Hover.stop(true);
      c.hide();
      if (io) io.unobserve(c.el);
      c.el.remove();
      S.cards.delete(k);
    }
  }

  /* ═════════════════════════════════════════ 선택(셸의 트레이와 공유) */
  const itemKey = p => `${p.drive}:dir:${p.did}`;
  function pushSel() {
    const items = [...S.sel.values()];
    root.classList.toggle('selmode', items.length > 0);
    try { if (ctx.select && ctx.select.set) ctx.select.set(items.map(x => x.key), items); } catch (e) { /* 셸 오류는 무시 */ }
  }
  function syncCardSel() {
    for (const c of S.cards.values()) c.el.classList.toggle('sel', S.sel.has(itemKey(c.p)));
    root.classList.toggle('selmode', S.sel.size > 0);
  }
  function toggleSel(p, range) {
    if (range && S.anchor) { rangeSel(p); return; }
    if (!p.connected) { toast('연결되지 않은 장치의 프로젝트는 고를 수 없어요', { kind: 'info' }); return; }
    const k = itemKey(p);
    if (S.sel.has(k)) S.sel.delete(k); else S.sel.set(k, itemOf(p));
    S.anchor = p.key;
    syncCardSel();
    pushSel();
  }
  function rangeSel(p) {
    const a = S.order.indexOf(S.anchor), b = S.order.indexOf(p.key);
    if (a < 0 || b < 0) { toggleSel(p, false); return; }
    const [x, y] = a < b ? [a, b] : [b, a];
    for (let i = x; i <= y; i++) {
      const c = S.cards.get(S.order[i]);
      if (c && c.p.connected) S.sel.set(itemKey(c.p), itemOf(c.p));
    }
    syncCardSel();
    pushSel();
  }
  if (ctx.select && typeof ctx.select.onChange === 'function') {
    const fn = arg => {
      if (!alive) return;
      let keys = null;
      if (arg instanceof Map) keys = [...arg.keys()];
      else if (arg instanceof Set) keys = [...arg];
      else if (Array.isArray(arg)) keys = arg.map(x => (typeof x === 'string' ? x : x && x.key));
      else if (arg && Array.isArray(arg.keys)) keys = arg.keys;
      else if (arg && Array.isArray(arg.items)) keys = arg.items.map(x => x.key);
      if (keys == null) keys = [];
      const ks = new Set(keys);
      let changed = false;
      for (const k of [...S.sel.keys()]) if (!ks.has(k)) { S.sel.delete(k); changed = true; }
      if (changed) syncCardSel();
    };
    const off = ctx.select.onChange(fn);
    if (typeof off === 'function') disposers.push(off);
  }

  /* ═════════════════════════════════════════ 불러오기 */
  function params() {
    const q = { group: S.group, sort: S.sort };
    if (S.q) q.q = S.q;
    if (S.drive !== '*') q.drive = S.drive;
    if (S.worker) q.worker = S.worker;
    if (S.group === 'month') { if (S.year) q.year = S.year; }
    else if (/^y\d{4}$/.test(S.period)) q.year = S.period.slice(1);
    return q;
  }
  async function load(o = {}) {
    const my = ++S.seq;
    S.loading = true;
    clearTimeout(S.loadT);
    S.loadT = setTimeout(() => { if (S.seq === my && S.loading) loadBar.classList.add('on'); }, 160);
    let d;
    try { d = await GET('/api/projects', params()); }
    catch (e) {
      if (my !== S.seq || !alive) return;
      S.loading = false; loadBar.classList.remove('on');
      if (!S.data) renderError(e); else toastErr(e, '프로젝트 목록을 불러오지 못했어요');
      return;
    }
    if (my !== S.seq || !alive) return;
    S.loading = false;
    loadBar.classList.remove('on');
    if (S.drive === '*') {
      const cnt = {};
      for (const g of d.groups) for (const p of g.projects) cnt[p.drive] = (cnt[p.drive] || 0) + 1;
      if (!S.q && !S.worker && S.period === 'all' && S.group !== 'month') S.allCounts = cnt;
    }
    if (S.group === 'month') S.year = d.year || S.year;
    applyPeriod(d);
    S.data = d;
    render(o.anim !== false && !o.quiet);
  }
  function applyPeriod(d) {
    if (S.group === 'month' || !/^d\d+$/.test(S.period)) return;
    const cut = dateCut(+S.period.slice(1));
    let total = 0, bytes = 0;
    d.groups = d.groups.map(g => {
      const ps = g.projects.filter(p => (p.date || '') >= cut);
      const cb = new Array(NCAT).fill(0);
      let b = 0;
      for (const p of ps) { b += p.size; (p.cat_bytes || []).forEach((x, i) => { cb[i] += x || 0; }); }
      total += ps.length; bytes += b;
      return Object.assign({}, g, { projects: ps, count: ps.length, bytes: b, cat_bytes: cb, subtitle: `프로젝트 ${ps.length}개` });
    }).filter(g => g.count);
    if (S.group === 'client') d.groups.sort((a, b) => b.bytes - a.bytes);
    d.total = total; d.total_bytes = bytes;
  }

  /* ═════════════════════════════════════════ 그리기 */
  function filtersOn() { return !!(S.q || S.drive !== '*' || S.worker || (S.period !== 'all' && S.group !== 'month')); }
  function renderHead() {
    const d = S.data;
    const nDr = new Set();
    let nOff = 0;
    if (d) for (const g of d.groups) for (const p of g.projects) { nDr.add(p.drive); if (!p.connected) nOff++; }
    if (!d) subEl.innerHTML = '';
    else if (!d.total) subEl.innerHTML = filtersOn() ? '<span class="dim">조건에 맞는 프로젝트가 없어요</span>' : '<span class="dim">스캔한 드라이브에서 프로젝트 폴더를 찾아 보여 드려요</span>';
    else {
      subEl.innerHTML = `<span><b>${d.total.toLocaleString('ko-KR')}개</b> 프로젝트</span><span class="dim">·</span><span><b>${esc(fmtSize(d.total_bytes))}</b></span>` +
        `<span class="dim">·</span><span class="dim">드라이브 ${nDr.size}곳${nOff ? ` · 연결 안 됨 ${nOff}개` : ''}</span>`;
    }
    ddDrive.refresh(); ddWorker.refresh(); ddPeriod.refresh(); ddSort.refresh();
    ddPeriod.el.hidden = S.group === 'month';
    bar.hidden = !!(d && !d.total && !filtersOn() && !(d.years && d.years.length));
    resetBtn.hidden = !filtersOn();
    root.dataset.group = S.group;
  }
  function render(anim) {
    renderHead();
    Hover.stop(true);
    const d = S.data;
    const build = () => {
      const frag = document.createDocumentFragment();
      const keep = new Set();
      S.order = [];
      if (!d.total) frag.append(emptyState(d));
      else if (S.group === 'client') buildClients(frag, d, keep, anim);
      else buildMonths(frag, d, keep, anim);
      body.replaceChildren(frag);
      gcCards(keep);
      if (S.group === 'client') body.querySelectorAll('.pj-rw').forEach(rw => rw._edges && rw._edges());
      if (S.group === 'month') trackMonth();
      raf2(() => { for (const c of S.cards.values()) if (c.vis && !c.pcb && c.posterState !== 'ready') { c.hide(); c.show(); } });
    };
    if (anim && !reduced && body.childElementCount) {
      body.classList.remove('swap-in');
      body.classList.add('swap-out');
      setTimeout(() => { if (!alive) return; build(); body.classList.remove('swap-out'); void body.offsetWidth; body.classList.add('swap-in'); }, 120);
    } else {
      build();
      if (anim && !reduced) { body.classList.remove('swap-in'); void body.offsetWidth; body.classList.add('swap-in'); }
    }
  }
  function stagger(el, i, anim) {
    if (!anim || reduced || i > 14) return;
    el.classList.remove('in');
    el.style.animationDelay = (i * 28) + 'ms';
    void el.offsetWidth;
    el.classList.add('in');
    setTimeout(() => { el.classList.remove('in'); el.style.animationDelay = ''; }, 600 + i * 28);
  }

  function buildClients(frag, d, keep, anim) {
    const maxB = Math.max(1, ...d.groups.map(g => g.bytes));
    d.groups.forEach((g, gi) => {
      const col = S.collapsed.has(g.key);
      const unk = g.title === '미분류';
      const sec = h('section', { class: 'pj-sec' + (col ? ' col' : ''), 'data-k': g.key });
      const sh = h('button', { class: 'pj-sh', type: 'button', 'aria-expanded': col ? 'false' : 'true' });
      sh.innerHTML = ic('chevD', 'cv') +
        `<span class="nm"><b class="${unk ? 'unk' : ''}">${esc(g.title)}</b><span class="num">${g.count}개</span></span>`;
      // 트랙 = 가장 큰 광고주, 채움 = 이 광고주(구성 색으로) — 섹션끼리 크기를 바로 견줄 수 있다
      const cb = h('div', { class: 'cb', 'data-tip': compTip(g.cat_bytes, g.bytes) });
      const track = h('div', { class: 'pj-mb md trk' });
      const fill = miniBar(g.cat_bytes, g.bytes, 'md' + (anim ? ' grow' : ''));
      fill.style.width = Math.max(1.5, g.bytes / maxB * 100).toFixed(2) + '%';
      track.append(fill);
      cb.append(track);
      sh.append(cb, h('span', { class: 'sz num', text: fmtSize(g.bytes) }));
      sh.addEventListener('click', () => toggleSec(sec, g.key));
      const rw = h('div', { class: 'pj-rw' });
      const row = h('div', { class: 'pj-row' });
      const al = h('button', { class: 'pj-arr l', type: 'button', html: ic('chevL'), 'aria-label': '앞으로', tabindex: '-1' });
      const ar = h('button', { class: 'pj-arr r', type: 'button', html: ic('chevR'), 'aria-label': '뒤로', tabindex: '-1' });
      g.projects.forEach((p, i) => {
        const c = cardFor(p);
        keep.add(p.key);
        S.order.push(p.key);
        row.append(c.el);
        if (gi < 3) stagger(c.el, i, anim);
      });
      rw.append(row, al, ar);
      const edges = () => {
        const max = row.scrollWidth - row.clientWidth;
        rw.classList.toggle('fl', row.scrollLeft > 4);
        rw.classList.toggle('fr', row.scrollLeft < max - 4);
      };
      rw._edges = edges;
      row.addEventListener('scroll', edges, { passive: true });
      if (window.ResizeObserver) { const ro = new ResizeObserver(edges); ro.observe(row); disposers.push(() => ro.disconnect()); }
      const step = dir => {
        const cw = (row.firstElementChild ? row.firstElementChild.offsetWidth : 250) + 16;
        const n = Math.max(1, Math.floor((row.clientWidth - 40) / cw));
        row.scrollBy({ left: dir * n * cw, behavior: reduced ? 'auto' : 'smooth' });
      };
      al.addEventListener('click', () => step(-1));
      ar.addEventListener('click', () => step(1));
      row.addEventListener('wheel', e => {
        // 세로 휠은 페이지 스크롤로 두고, Shift+휠·가로 휠만 줄을 넘긴다
        if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
        if (e.shiftKey) { e.preventDefault(); row.scrollLeft += e.deltaY; }
      }, { passive: false });
      sec.append(sh, h('div', { class: 'pj-sb' }, h('div', { class: 'pj-sbi' }, rw)));
      frag.append(sec);
    });
  }
  function toggleSec(sec, key) {
    const col = !sec.classList.contains('col');
    sec.classList.add('anim');
    clearTimeout(sec._at);
    sec._at = setTimeout(() => sec.classList.remove('anim'), reduced ? 0 : 460);
    sec.classList.toggle('col', col);
    sec.querySelector('.pj-sh').setAttribute('aria-expanded', col ? 'false' : 'true');
    if (col) S.collapsed.add(key); else S.collapsed.delete(key);
    store.set('collapsed', [...S.collapsed].slice(-200));
    if (!col) { const rw = sec.querySelector('.pj-rw'); rw && rw._edges && requestAnimationFrame(rw._edges); }
  }

  function buildMonths(frag, d, keep, anim) {
    if (S.group === 'month') frag.append(yearStrip(d));
    d.groups.forEach((g, gi) => {
      const sec = h('section', { class: 'pj-mon', 'data-k': g.key, id: 'pjm-' + g.key });
      const mh = h('div', { class: 'pj-mh' });
      mh.innerHTML = `<h3>${esc(g.title)}</h3><span class="s num">${g.count}개</span><span class="sp"></span><span class="v num">${esc(fmtSize(g.bytes))}</span>`;
      const grid = h('div', { class: 'pj-grid' });
      g.projects.forEach((p, i) => {
        const c = cardFor(p);
        keep.add(p.key);
        S.order.push(p.key);
        grid.append(c.el);
        if (gi < 2) stagger(c.el, i, anim);
      });
      sec.append(mh, grid);
      frag.append(sec);
    });
  }
  function yearStrip(d) {
    const wrap = h('div', { class: 'pj-ysum' });
    const ys = d.years || [];
    const chips = h('div', { class: 'pj-years', role: 'tablist' });
    for (const y of ys) {
      const n = y.months.reduce((a, m) => a + m.count, 0);
      const b = h('button', { class: 'chip' + (y.year === S.year ? ' on' : ''), type: 'button', role: 'tab', 'aria-selected': y.year === S.year ? 'true' : 'false' });
      b.innerHTML = `<span>${y.year}년</span><em class="num">${n}</em>`;
      b.addEventListener('click', () => { if (S.year !== y.year) { S.year = y.year; load(); } });
      chips.append(b);
    }
    const yr = ys.find(y => y.year === S.year) || ys[0];
    const chart = h('div', { class: 'pj-chart' });
    if (yr) {
      const tb = yr.months.reduce((a, m) => a + m.bytes, 0);
      const tn = yr.months.reduce((a, m) => a + m.count, 0);
      chart.append(h('div', { class: 'hd', html: `<h4>${yr.year}년 월별 용량</h4><span class="num">${tn}개 · ${esc(fmtSize(tb))}</span><span class="sp"></span><span>막대를 누르면 그 달로 가요</span>` }));
      const cols = h('div', { class: 'pj-cols' });
      const mx = Math.max(1, ...yr.months.map(m => m.bytes));
      for (const m of yr.months) {
        const key = `${yr.year}-${String(m.month).padStart(2, '0')}`;
        const has = !!m.count && d.groups.some(g => g.key === key);
        const b = h('button', {
          class: 'pj-col' + (m.count ? '' : ' zero'), type: 'button', 'data-m': key, disabled: has ? null : true,
          'data-tip': m.count ? `${yr.year}년 ${m.month}월\n프로젝트 ${m.count}개 · ${fmtSize(m.bytes)}` : `${yr.year}년 ${m.month}월 · 프로젝트 없음`,
        });
        const bar = h('i');
        b.append(h('span', { class: 'tk' }, bar), h('span', { class: 'ml', text: m.month + '월' }));
        const pct = m.bytes ? Math.max(3, Math.sqrt(m.bytes / mx) * 100) : 0;
        if (reduced) bar.style.height = pct + '%';
        else raf2(() => { bar.style.height = pct + '%'; });
        b.addEventListener('click', () => scrollToMonth(key));
        cols.append(b);
      }
      chart.append(cols);
    }
    wrap.append(chips, chart);
    return wrap;
  }
  function scrollToMonth(key) {
    const sec = body.querySelector(`#pjm-${CSS.escape(key)}`);
    if (!sec) return;
    const sc = scrollEl();
    const top = sec.getBoundingClientRect().top - (sc.getBoundingClientRect ? sc.getBoundingClientRect().top : 0) + sc.scrollTop - (bar.offsetHeight - 2);
    sc.scrollTo({ top, behavior: reduced ? 'auto' : 'smooth' });
    markMonth(key);
  }
  function markMonth(key) { body.querySelectorAll('.pj-col').forEach(b => b.classList.toggle('cur', b.dataset.m === key)); }
  function trackMonth() {
    const secs = [...body.querySelectorAll('.pj-mon')];
    if (!secs.length) return;
    const sc = scrollEl();
    const top0 = (sc.getBoundingClientRect ? sc.getBoundingClientRect().top : 0) + bar.offsetHeight + 40;
    let cur = secs[0].dataset.k;
    for (const s of secs) { if (s.getBoundingClientRect().top <= top0) cur = s.dataset.k; else break; }
    markMonth(cur);
  }

  function emptyState(d) {
    const box = h('div', { class: 'pj-empty' });
    const e = h('div', { class: 'empty' });
    box.append(e);
    if (filtersOn()) {
      e.append(h('div', { class: 'ill dim', html: ic('search') }), h('h3', { text: '조건에 맞는 프로젝트가 없어요' }),
        h('p', { text: '검색어나 필터를 바꾸거나 모두 풀어 보세요.' }),
        h('div', { class: 'pj-acts' }, h('button', { class: 'btn', type: 'button', text: '필터 모두 풀기', onclick: resetFilters })));
      return box;
    }
    if (!d.scanned) {
      e.append(h('div', { class: 'ill dim', html: ic('drive') }), h('h3', { text: '아직 스캔한 드라이브가 없어요' }),
        h('p', { text: '드라이브를 스캔하면 그 안의 프로젝트 폴더를 찾아 광고주별·월별로 모아 보여 드려요.' }));
      return box;
    }
    e.append(h('div', { class: 'ill', html: ic('folderPlus') }),
      h('h3', { text: '프로젝트 폴더를 찾지 못했어요' }),
      h('p', { text: '프로젝트를 모아 둔 폴더를 지정해 주세요. 그 폴더 바로 아래의 폴더들을 프로젝트로 보여 드려요.' }));
    const row = h('div', { class: 'pj-acts' });
    const pick = h('button', { class: 'btn primary', type: 'button', html: ic('folder') + '<span>폴더 지정</span>' });
    row.append(pick);
    e.append(row);
    const addRow = h('div', { class: 'addrow', hidden: true });
    const inp = h('input', { class: 'tin', type: 'text', placeholder: '예: D:\\Projects', spellcheck: 'false', 'aria-label': '프로젝트 폴더 경로' });
    const add = h('button', { class: 'btn', type: 'button', text: '추가' });
    addRow.append(inp, add);
    e.append(addRow);
    e.append(h('div', { class: 'tips' }, h('span', { text: '이름이 날짜로 시작하는 폴더 (260803_…)' }), h('span', { text: 'aep · prproj · drp 가 든 폴더' }), h('span', { text: '지정한 폴더의 바로 아래 폴더' })));
    const submit = async v => {
      v = String(v || '').trim();
      if (!v) { inp.focus(); return; }
      add.disabled = true; pick.disabled = true;
      try {
        await POST('/api/projects/roots', { add: v });
        toast('프로젝트 폴더를 추가했어요', { kind: 'ok', sub: v });
        S.settingsCache = null;
        load();
      } catch (er) { toastErr(er, '폴더를 추가하지 못했어요'); }
      finally { add.disabled = false; pick.disabled = false; }
    };
    pick.addEventListener('click', async () => {
      const p = await pickFolder();
      if (p === false) { addRow.hidden = false; inp.focus(); return; }
      if (p) submit(p);
    });
    add.addEventListener('click', () => submit(inp.value));
    inp.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); submit(inp.value); } });
    return box;
  }
  /* 폴더 선택: 경로 문자열 · null(취소) · false(창 없음 → 경로 입력) */
  let metaP = null;
  async function pickFolder(initial) {
    if (!metaP) metaP = GET('/api/meta').catch(() => ({}));
    const meta = await metaP;
    if (meta && meta.dialog === false) return false;   // 창이 없는 실행(브라우저 모드) → 경로 입력
    try {
      const r = await POST('/api/pick_folder', initial ? { initial } : {});
      if (r.canceled || !r.path) return null;
      return r.path;
    } catch (e) {
      if (errCode(e) === 'no_dialog') return false;
      toastErr(e, '폴더 선택 창을 열지 못했어요');
      return false;
    }
  }
  function renderError(e) {
    body.replaceChildren(h('div', { class: 'pj-empty' }, h('div', { class: 'empty' },
      h('div', { class: 'ill dim', html: ic('info') }), h('h3', { text: '프로젝트 목록을 불러오지 못했어요' }),
      h('p', { text: (e && e.message) || '잠시 뒤에 다시 시도해 주세요.' }),
      h('div', { class: 'pj-acts' }, h('button', { class: 'btn', type: 'button', text: '다시 시도', onclick: () => load() })))));
  }
  function resetFilters() {
    S.q = ''; qIn.value = ''; search.classList.remove('has');
    S.drive = '*'; S.worker = ''; S.period = 'all';
    load();
  }

  /* ── 상단 동작 */
  function setGroup(id) {
    S.group = id;
    store.set('group', id);
    tabs.set(id);
    const sc = scrollEl();
    if (sc && sc.scrollTop > head.offsetHeight) sc.scrollTo({ top: 0, behavior: 'auto' });
    load();
  }
  let qT = 0;
  qIn.addEventListener('input', () => {
    search.classList.toggle('has', !!qIn.value);
    clearTimeout(qT);
    qT = setTimeout(() => { const v = qIn.value.trim(); if (v !== S.q) { S.q = v; load({ anim: false }); } }, 180);
  });
  qIn.addEventListener('keydown', e => { if (e.key === 'Escape' && qIn.value) { e.preventDefault(); e.stopPropagation(); qIn.value = ''; qIn.dispatchEvent(new Event('input')); } });
  search.querySelector('.clr').addEventListener('click', () => { qIn.value = ''; qIn.dispatchEvent(new Event('input')); qIn.focus(); });
  resetBtn.addEventListener('click', resetFilters);
  btnOrg.addEventListener('click', () => openOrganizer());

  /* 스크롤: 도구 줄 그림자 · 월 표시 */
  const onScroll = () => {
    const sc = scrollEl();
    const rTop = sc.getBoundingClientRect ? sc.getBoundingClientRect().top : 0;
    bar.classList.toggle('stuck', bar.getBoundingClientRect().top - rTop <= 0.5 && (sc.scrollTop || 0) > head.offsetTop + 4);
    if (S.group === 'month') trackMonth();
    if (S.group !== 'client') {
      const lim = bar.getBoundingClientRect().bottom;
      for (const mh of body.querySelectorAll('.pj-mh')) {
        const t = mh.getBoundingClientRect().top;
        mh.classList.toggle('stuck', Math.abs(t - lim) < 1.5 && mh.parentElement.getBoundingClientRect().top < t - 1);
      }
    }
  };
  let scT = 0;
  const scHandler = () => { if (!scT) scT = requestAnimationFrame(() => { scT = 0; onScroll(); }); };
  on(document, 'scroll', scHandler, { capture: true, passive: true });
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(() => { root.style.setProperty('--pj-bar-h', bar.offsetHeight + 'px'); measure(); });
    ro.observe(bar);
    ro.observe(root);
    disposers.push(() => ro.disconnect());
  }

  /* ═════════════════════════════════════════ 오른쪽 시트(공통) */
  let topSide = null;
  function Side(o) {
    const scrim = h('div', { class: 'pj-scrim' });
    const el = h('div', { class: 'pj-side ' + (o.cls || ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': o.label || '', style: { '--side-w': (o.width || 980) + 'px' } });
    const hd = h('div', { class: 'sd-h' });
    const bd = h('div', { class: 'sd-b' });
    const ft = h('div', { class: 'sd-f' });
    const x = h('button', { class: 'ibtn', type: 'button', html: ic('x'), 'data-tip': '닫기 (Esc)', 'aria-label': '닫기' });
    el.append(hd, bd, ft);
    document.body.append(scrim, el);
    const prevFocus = document.activeElement;
    let closed = false;
    const me = {
      el, hd, bd, ft, x, onKey: null, onClose: null,
      close() {
        if (closed) return;
        closed = true;
        me.onClose && me.onClose();
        document.removeEventListener('keydown', key, true);
        if (topSide === me) topSide = me.prev || null;
        el.classList.remove('show'); scrim.classList.remove('show');
        el.classList.add('closing');
        setTimeout(() => { el.remove(); scrim.remove(); }, reduced ? 0 : 280);
        if (prevFocus && prevFocus.isConnected && prevFocus.focus) prevFocus.focus({ preventScroll: true });
      },
    };
    me.prev = topSide;
    topSide = me;
    x.addEventListener('click', () => me.close());
    scrim.addEventListener('click', () => me.close());
    const key = e => {
      if (topSide !== me) return;
      // 셸의 확인 시트·팝오버가 위에 떠 있으면 그쪽이 먼저
      const layer = document.getElementById('layer');
      if (layer && layer.querySelector('.sheet')) return;
      if (openPop && !el.contains(openPop.el) && e.key === 'Escape') return;
      if (document.querySelector('.pop.show') && !openPop && e.key === 'Escape') return;
      if (e.key === 'Escape') {
        if (inField(e) && e.target.value && !e.target.dataset.escClose) return;
        e.preventDefault(); e.stopPropagation(); me.close(); return;
      }
      if (me.onKey && me.onKey(e)) { e.preventDefault(); e.stopPropagation(); return; }
      // 시트가 떠 있는 동안 셸의 전역 단축키(뒤로·삭제)가 뒤 화면에 닿지 않게
      if (!inField(e) && (e.key === 'Backspace' || e.key === 'Delete')) { e.preventDefault(); e.stopPropagation(); }
    };
    document.addEventListener('keydown', key, true);
    requestAnimationFrame(() => { scrim.classList.add('show'); el.classList.add('show'); });
    return me;
  }
  disposers.push(() => { let s = topSide; while (s) { const p = s.prev; s.close(); s = p; } });

  /* ═════════════════════════════════════════ 플레이어 */
  function Player() {
    const el = h('div', { class: 'pp', tabindex: '-1' });
    const va = h('video', { playsinline: '', preload: 'metadata', disablepictureinpicture: '' });
    const vb = h('video', { playsinline: '', preload: 'metadata', disablepictureinpicture: '' });
    const po = h('img', { class: 'pp-po', alt: '' });
    const big = h('button', { class: 'pp-big', type: 'button', html: ic('play'), 'aria-label': '재생' });
    const flash = h('div', { class: 'pp-flash' });
    const st = h('div', { class: 'pp-st' }, h('div', { class: 'rg', html: ringSvg() }), h('div', { class: 'ic', html: ic('nofilm') }), h('div', { class: 'tx' }));
    const pbar = h('div', { class: 'pp-bar' });
    const bPlay = h('button', { class: 'pp-b pl', type: 'button', html: ic('play'), 'data-tip': '재생 (Space)' });
    const tCur = h('span', { class: 'pp-t num', text: '0:00' });
    const sk = h('div', { class: 'pp-sk', role: 'slider', 'aria-label': '재생 위치', tabindex: '-1' });
    sk.innerHTML = '<div class="tr"><i class="bf"></i><i class="hv"></i><i class="fi"></i></div><span class="bub">0:00</span>';
    const tRem = h('span', { class: 'pp-t r num', text: '-0:00' });
    const vol = h('div', { class: 'pp-vol' });
    const bVol = h('button', { class: 'pp-b', type: 'button', html: ic('vol'), 'data-tip': '소리 (M)' });
    const vs = h('div', { class: 'pp-vs', role: 'slider', 'aria-label': '음량' });
    vs.innerHTML = '<div class="tr"><i class="fi"></i></div>';
    vol.append(bVol, vs);
    const bFs = h('button', { class: 'pp-b', type: 'button', html: ic('fs'), 'data-tip': '전체 화면 (F)' });
    pbar.append(bPlay, tCur, sk, tRem, vol, bFs);
    el.append(va, vb, po, big, flash, st, pbar);
    const fi = sk.querySelector('.fi'), bf = sk.querySelector('.bf'), hv = sk.querySelector('.hv'), bub = sk.querySelector('.bub');
    const vfi = vs.querySelector('.fi');
    let v = va, m = null, tok = 0, rafId = 0, idleT = 0, dragging = false;
    let volume = clamp(Number(store.get('vol', 0.8)), 0, 1), muted = !!store.get('muted', false);
    [va, vb].forEach(x => { x.volume = volume; x.muted = muted; });

    const isPlaying = () => !v.paused && !v.ended;
    function syncBtns() {
      const pl = isPlaying();
      bPlay.innerHTML = ic(pl ? 'pause' : 'play');
      bPlay.setAttribute('data-tip', pl ? '일시 정지 (Space)' : '재생 (Space)');
      bPlay.setAttribute('aria-label', pl ? '일시 정지' : '재생');
      bVol.innerHTML = ic(muted || volume === 0 ? 'mute' : 'vol');
      vfi.style.width = ((muted ? 0 : volume) * 100) + '%';
      el.classList.toggle('playing', pl);
    }
    function frame() {
      rafId = 0;
      const d = v.duration || durOf(m) || 0;
      if (d && !dragging) {
        fi.style.width = (v.currentTime / d * 100) + '%';
        tCur.textContent = fmtTime(v.currentTime);
        tRem.textContent = '-' + fmtTime(Math.max(0, d - v.currentTime));
      }
      try { if (v.buffered.length && d) bf.style.width = (v.buffered.end(v.buffered.length - 1) / d * 100) + '%'; } catch (e) { /* 무시 */ }
      if (isPlaying()) rafId = requestAnimationFrame(frame);
    }
    const kick = () => { if (!rafId) rafId = requestAnimationFrame(frame); };
    function wake() {
      el.classList.remove('idle');
      clearTimeout(idleT);
      if (isPlaying()) idleT = setTimeout(() => { if (isPlaying() && !dragging && !pbar.matches(':hover')) el.classList.add('idle'); }, 2400);
    }
    [va, vb].forEach(x => {
      x.addEventListener('play', () => { if (x === v) { el.classList.add('started'); syncBtns(); kick(); wake(); } });
      x.addEventListener('pause', () => { if (x === v) { syncBtns(); wake(); kick(); } });
      x.addEventListener('timeupdate', () => { if (x === v) kick(); });
      x.addEventListener('loadedmetadata', () => { if (x === v && m) { noteInfo(m.path, { duration: x.duration, width: x.videoWidth, height: x.videoHeight }); kick(); me.onMeta && me.onMeta(m); } });
      x.addEventListener('error', () => { if (x === v && x.getAttribute('src')) onErr(); });
    });
    function status(kind, text) {
      el.classList.toggle('busy', kind === 'busy');
      el.classList.toggle('nop', kind === 'nop');
      st.querySelector('.tx').textContent = text || '';
    }
    let triedProxy = false;
    async function onErr() {
      const my = tok;
      if (!triedProxy && m && m.video_url) { triedProxy = true; await viaProxy(my, true); return; }
      status('nop', '이 영상은 미리볼 수 없어요');
      el.classList.remove('started');
    }
    async function viaProxy(my, autoplay) {
      if (PROXY.has(m.path) && PROXY.get(m.path).s === 'failed') { status('nop', '재생용 미리보기를 만들 수 없어요'); return; }
      status('busy', '재생용 미리보기를 만드는 중이에요');
      const url = await getProxy(m, null, me.sig);
      if (tok !== my) return;
      if (!url) { status('nop', '재생용 미리보기를 만들 수 없어요'); return; }
      status(null);
      setSrc(url, autoplay, my);
    }
    function setSrc(url, autoplay, my) {
      const back = v === va ? vb : va;
      const front = v;
      if (tok !== my) return;
      const go = () => {
        if (tok !== my) return;
        back.classList.add('on');
        front.classList.remove('on');
        v = back;
        setTimeout(() => { if (front !== v) { try { front.pause(); } catch (e) { /* 무시 */ } front.removeAttribute('src'); try { front.load(); } catch (e) { /* 무시 */ } } }, 320);
        fi.style.width = '0%'; bf.style.width = '0%';
        syncBtns(); kick();
        if (autoplay) { const p = v.play(); if (p && p.catch) p.catch(() => { syncBtns(); }); }
      };
      back.addEventListener('loadeddata', go, { once: true });
      back.src = url;
      back.volume = volume; back.muted = muted;
      try { back.load(); } catch (e) { /* 무시 */ }
    }
    const me = {
      el, sig: { stop: false }, onMeta: null,
      load(media, autoplay) {
        tok++;
        const my = tok;
        me.sig.stop = true;
        me.sig = { stop: false };
        m = media; triedProxy = false;
        status(null);
        el.classList.toggle('started', !!autoplay && !!(v.getAttribute('src')));
        if (!autoplay) el.classList.remove('started');
        tCur.textContent = '0:00';
        const d = durOf(m);
        tRem.textContent = '-' + fmtTime(d);
        fi.style.width = '0%';
        // 포스터
        po.classList.remove('on');
        if (m) Posters.want(m, r => { if (tok === my && r.s === 'ready') { po.onload = () => po.classList.add('on'); po.src = r.url; if (po.complete && po.naturalWidth) po.classList.add('on'); } });
        if (!m || m.offline) { status('nop', m ? '연결되지 않은 장치의 영상이에요' : '이 프로젝트에는 영상이 없어요'); return; }
        if (playOf(m) === 'none' && !m.video_url) { status('nop', '이 영상은 미리볼 수 없어요'); return; }
        const px = PROXY.get(m.path);
        if (px && px.s === 'ready') { setSrc(px.url, autoplay, my); return; }
        if (playOf(m) === 'proxy' || !m.video_url) { triedProxy = true; if (autoplay) viaProxy(my, true); else me._needProxy = true; return; }
        me._needProxy = false;
        setSrc(m.video_url, autoplay, my);
      },
      toggle() {
        if (el.classList.contains('nop')) return;
        if (me._needProxy) { me._needProxy = false; viaProxy(tok, true); return; }
        if (!v.getAttribute('src')) return;
        if (isPlaying()) v.pause(); else { const p = v.play(); if (p && p.catch) p.catch(() => {}); }
        flash.innerHTML = ic(isPlaying() ? 'pause' : 'play');
        flash.classList.remove('go'); void flash.offsetWidth; flash.classList.add('go');
      },
      seekBy(s) { if (!v.duration) return; v.currentTime = clamp(v.currentTime + s, 0, v.duration - 0.05); kick(); wake(); },
      mute() { muted = !muted; if (!muted && volume === 0) volume = 0.6; apply(); },
      fullscreen() {
        if (document.fullscreenElement) { document.exitFullscreen && document.exitFullscreen().catch(() => {}); return; }
        if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
      },
      destroy() {
        tok++; me.sig.stop = true;
        cancelAnimationFrame(rafId); clearTimeout(idleT);
        [va, vb].forEach(x => { try { x.pause(); } catch (e) { /* 무시 */ } x.removeAttribute('src'); try { x.load(); } catch (e) { /* 무시 */ } });
        if (document.fullscreenElement === el) document.exitFullscreen().catch(() => {});
      },
      get video() { return v; },
    };
    function apply() {
      [va, vb].forEach(x => { x.volume = volume; x.muted = muted; });
      store.set('vol', volume); store.set('muted', muted);
      syncBtns();
    }
    big.addEventListener('click', e => { e.stopPropagation(); me.toggle(); });
    bPlay.addEventListener('click', () => me.toggle());
    bVol.addEventListener('click', () => me.mute());
    bFs.addEventListener('click', () => me.fullscreen());
    el.addEventListener('click', e => { if (e.target === el || e.target.tagName === 'VIDEO' || e.target === po) me.toggle(); });
    el.addEventListener('dblclick', e => { if (e.target.tagName === 'VIDEO') me.fullscreen(); });
    el.addEventListener('pointermove', wake);
    el.addEventListener('pointerleave', () => { if (isPlaying()) { clearTimeout(idleT); idleT = setTimeout(() => el.classList.add('idle'), 900); } });
    const fsCh = () => {
      const fs = document.fullscreenElement === el;
      el.classList.toggle('fs', fs);
      bFs.innerHTML = ic(fs ? 'fsx' : 'fs');
      bFs.setAttribute('data-tip', fs ? '전체 화면 끝내기 (F)' : '전체 화면 (F)');
    };
    document.addEventListener('fullscreenchange', fsCh);
    const oldDestroy = me.destroy;
    me.destroy = () => { document.removeEventListener('fullscreenchange', fsCh); oldDestroy(); };
    // 스크러버
    const ratio = (e, box) => { const r = box.getBoundingClientRect(); return clamp((e.clientX - r.left) / r.width, 0, 1); };
    sk.addEventListener('pointermove', e => {
      const f = ratio(e, sk), d = v.duration || durOf(m) || 0;
      hv.style.width = (f * 100) + '%';
      bub.textContent = fmtTime(f * d);
      const w = sk.getBoundingClientRect().width;
      bub.style.left = clamp(f * w, 18, w - 18) + 'px';
      if (dragging && v.duration) { v.currentTime = f * v.duration; fi.style.width = (f * 100) + '%'; tCur.textContent = fmtTime(f * v.duration); tRem.textContent = '-' + fmtTime(v.duration * (1 - f)); }
    });
    sk.addEventListener('pointerdown', e => {
      if (!v.duration) return;
      dragging = true; sk.classList.add('drag'); sk.setPointerCapture(e.pointerId);
      const f = ratio(e, sk);
      v.currentTime = f * v.duration; fi.style.width = (f * 100) + '%';
    });
    const endDrag = () => { if (!dragging) return; dragging = false; sk.classList.remove('drag'); kick(); wake(); };
    sk.addEventListener('pointerup', endDrag);
    sk.addEventListener('pointercancel', endDrag);
    let vdrag = false;
    const setVol = e => { volume = ratio(e, vs); muted = volume === 0; apply(); };
    vs.addEventListener('pointerdown', e => { vdrag = true; vs.classList.add('drag'); vs.setPointerCapture(e.pointerId); setVol(e); });
    vs.addEventListener('pointermove', e => { if (vdrag) setVol(e); });
    const vEnd = () => { vdrag = false; vs.classList.remove('drag'); };
    vs.addEventListener('pointerup', vEnd);
    vs.addEventListener('pointercancel', vEnd);
    syncBtns();
    return me;
  }

  /* ═════════════════════════════════════════ 프로젝트 시트 */
  let sheet = null;
  function openProject(p, card) {
    Hover.stop(true);
    if (sheet) sheet.close();
    const sd = Side({ cls: 'ps', label: p.title || p.name, width: 1000 });
    sheet = sd;
    const pl = Player();
    sd.onClose = () => { pl.destroy(); if (sheet === sd) sheet = null; if (card && card.el.isConnected) card.el.focus({ preventScroll: true }); };
    // 머리
    const unk = !p.client || p.client === '미분류';
    const ey = h('div', { class: 'ey' }, h('span', { class: 'pj-cchip' + (unk ? ' unk' : ''), text: p.client || '미분류' }), h('span', { class: 'num', text: fmtYMD(p.date, true) }));
    sd.hd.append(h('div', { class: 'tt' }, ey, h('h2', { text: p.title || p.name }), p.title && p.title !== p.name ? h('p', { text: p.name }) : null), sd.x);
    // 본문(먼저 뼈대)
    const top = h('div', { class: 'ps-top' });
    const list = h('div', { class: 'ps-list' });
    list.append(h('div', { class: 'hd', html: '<span>영상</span>' }), h('div', { class: 'sc' }, ...Array.from({ length: 4 }, () => h('div', { class: 'ps-v' }, h('div', { class: 'th ps-sk' }), h('div', { class: 'tx' }, h('div', { class: 'ps-sk', style: { height: '10px', width: '70%' } }), h('div', { class: 'ps-sk', style: { height: '8px', width: '40%', marginTop: '7px' } }))))));
    top.append(pl.el, list);
    const cols = h('div', { class: 'ps-cols' });
    const meta = h('div', { class: 'ps-card' });
    const comp = h('div', { class: 'ps-card' });
    cols.append(meta, comp);
    sd.bd.append(top, cols);
    fillMeta(meta, p, null);
    fillComp(comp, p, null);
    pl.load(p.preview, false);
    // 동작
    const it = itemOf(p);
    const dr = driveByKey(p.drive);
    const noBin = dr ? dr.has_bin === false : (p.drive_kind === 'network' || p.drive_kind === 'usb');
    const off = !p.connected;
    const bReveal = h('button', { class: 'btn', type: 'button', html: ic('reveal') + '<span>탐색기에서 열기</span>', disabled: off });
    const bMove = h('button', { class: 'btn primary', type: 'button', html: ic('archive') + '<span>다른 위치로 보관</span>', disabled: off, 'data-tip': '외장 디스크·NAS·다른 드라이브로 옮겨요' });
    const bCopy = h('button', { class: 'btn', type: 'button', html: ic('copy') + '<span>복사</span>', disabled: off, 'data-tip': '원본은 그대로 두고 다른 곳에 복사해요' });
    const bBin = h('button', { class: 'btn danger', type: 'button', html: ic('trash') + `<span>${noBin ? '삭제(복구 불가)' : '휴지통'}</span>`, disabled: off });
    const bNot = h('button', { class: 'btn ghost', type: 'button', html: ic('notp') + '<span>프로젝트가 아니에요</span>', 'data-tip': '이 폴더를 프로젝트 목록에서 빼요(파일은 그대로)' });
    if (off) sd.ft.append(h('span', { class: 'og-empty', style: { padding: '0 6px 0 0' }, text: '연결되지 않은 장치라 다룰 수 없어요' }));
    sd.ft.append(bReveal, bMove, bCopy, bBin, h('div', { class: 'sp' }), bNot);
    const act = fn => () => { try { fn(); } catch (e) { toastErr(e); } };
    bReveal.addEventListener('click', act(() => (ctx.reveal ? ctx.reveal(p.path) : POST('/api/reveal', { path: p.path }).catch(e => toastErr(e)))));
    bMove.addEventListener('click', act(() => { if (ctx.openMove) { sd.close(); ctx.openMove([it], 'move'); } }));
    bCopy.addEventListener('click', act(() => { if (ctx.openMove) { sd.close(); ctx.openMove([it], 'copy'); } }));
    bBin.addEventListener('click', act(() => {
      const fn = noBin && typeof ctx.openDelete === 'function' ? ctx.openDelete : ctx.openRecycle;
      if (fn) { sd.close(); fn([it]); }
    }));
    bNot.addEventListener('click', async () => {
      bNot.disabled = true;
      try {
        await POST('/api/projects/not_project', { path: p.path });
        sd.close();
        const c = S.cards.get(p.key);
        if (c && !reduced) { c.el.classList.add('gone'); await sleep(300); }
        S.sel.delete(itemKey(p)); pushSel();
        S.settingsCache = null;
        load({ quiet: true });
        toast('프로젝트 목록에서 뺐어요', {
          kind: 'ok', sub: p.name,
          actions: [{ label: '되돌리기', primary: true, onClick: async () => { try { await POST('/api/projects/not_project', { path: p.path, remove: true }); S.settingsCache = null; load({ quiet: true }); } catch (e) { toastErr(e, '되돌리지 못했어요'); } } }],
        });
      } catch (e) { bNot.disabled = false; toastErr(e, '목록에서 빼지 못했어요'); }
    });
    // 키보드
    sd.onKey = e => {
      if (inField(e) || e.ctrlKey || e.metaKey || e.altKey) return false;
      const k = e.key;
      if (k === ' ' || k === 'k' || k === 'K') { pl.toggle(); return true; }
      if (k === 'ArrowLeft') { pl.seekBy(e.shiftKey ? -1 : -5); return true; }
      if (k === 'ArrowRight') { pl.seekBy(e.shiftKey ? 1 : 5); return true; }
      if (k === 'f' || k === 'F') { pl.fullscreen(); return true; }
      if (k === 'm' || k === 'M') { pl.mute(); return true; }
      if (k === 'ArrowDown' || k === 'ArrowUp') { return pickVideo(k === 'ArrowDown' ? 1 : -1); }
      return false;
    };
    let vids = [], vi = 0, vEls = [];
    function pickVideo(dir) {
      if (!vids.length) return false;
      const n = clamp(vi + dir, 0, vids.length - 1);
      if (n === vi) return true;
      choose(n, true);
      vEls[n] && vEls[n].scrollIntoView({ block: 'nearest', behavior: reduced ? 'auto' : 'smooth' });
      return true;
    }
    function choose(i, autoplay) {
      vi = i;
      vEls.forEach((x, j) => { x.classList.toggle('on', j === i); x.setAttribute('aria-current', j === i ? 'true' : 'false'); });
      pl.load(vids[i], autoplay);
    }
    requestAnimationFrame(() => pl.el.focus({ preventScroll: true }));
    // 상세 불러오기
    GET('/api/project', { key: p.key }).then(r => {
      if (sheet !== sd) return;
      const pr = r.project || p;
      vids = (r.videos || []).filter(Boolean);
      fillMeta(meta, pr, r);
      fillComp(comp, pr, r.children || []);
      const sc = h('div', { class: 'sc' });
      vEls = [];
      const lio = 'IntersectionObserver' in window ? new IntersectionObserver(es => { for (const e of es) if (e.isIntersecting) { lio.unobserve(e.target); e.target._load && e.target._load(); } }, { root: sc, rootMargin: '120px' }) : null;
      const curPath = pr.preview && pr.preview.path;
      vi = Math.max(0, vids.findIndex(x => x.path === curPath));
      vids.forEach((vm, i) => {
        const th = h('div', { class: 'th' });
        th.innerHTML = ic(PCAT_ICON[vm.cat] || 'film');
        const im = h('img', { alt: '' });
        const dd = h('span', { class: 'd num', text: durOf(vm) ? fmtTime(durOf(vm)) : '' });
        th.append(im, dd);
        const b = h('button', { class: 'ps-v' + (i === vi ? ' on' : ''), type: 'button', 'aria-current': i === vi ? 'true' : 'false' },
          th, h('div', { class: 'tx' }, h('div', { class: 'n', text: vm.name, 'data-tip': vm.path }),
            h('div', { class: 'm num', html: `<i class="c" style="--sc:${catVar(vm.cat == null ? 10 : vm.cat)}"></i>${esc(catName(vm.cat == null ? 10 : vm.cat))} · ${esc(fmtTs(vm.mtime))} · ${esc(fmtSize(vm.size))}` })));
        b._load = () => Posters.want(vm, res => { if (res.s === 'ready') { im.onload = () => im.classList.add('on'); im.src = res.url; if (im.complete && im.naturalWidth) im.classList.add('on'); } dd.textContent = durOf(vm) ? fmtTime(durOf(vm)) : ''; });
        b.addEventListener('click', () => { if (i !== vi) choose(i, true); else pl.toggle(); });
        vEls.push(b);
        sc.append(b);
        if (lio) lio.observe(b); else b._load();
      });
      sd.onClose = (old => () => { lio && lio.disconnect(); old(); })(sd.onClose);
      list.replaceChildren(h('div', { class: 'hd', html: `<span>영상</span><b class="num">${vids.length}${vids.length >= 200 ? '+' : ''}개</b><span class="sp"></span>${vids.length ? '<span class="num" style="color:var(--text4)">↑↓ 로 넘기기</span>' : ''}` }), sc);
      if (!vids.length) { sc.append(h('div', { class: 'og-empty', style: { padding: '10px 12px' }, text: '이 폴더에는 영상이 없어요' })); top.classList.add('solo'); list.remove(); }
      // 더 정확한 대표 영상으로(재생 가능성·길이)
      if (vids[vi] && (!p.preview || vids[vi].path !== p.preview.path || vids[vi].playable !== p.preview.playable)) pl.load(vids[vi], false);
      else if (vids[vi]) noteInfo(vids[vi].path, vids[vi]);
    }).catch(e => {
      if (sheet !== sd) return;
      list.replaceChildren(h('div', { class: 'hd', html: '<span>영상</span>' }), h('div', { class: 'og-empty', style: { padding: '10px 14px' }, text: e.message || '영상 목록을 불러오지 못했어요' }));
    });
  }
  function fillMeta(box, p, r) {
    const d = driveByKey(p.drive);
    const kindTxt = { ssd: 'SSD', hdd: 'HDD', usb: 'USB 메모리', network: 'NAS', unknown: '' }[p.drive_kind] || '';
    const ext = d && d.external ? '외장 ' : '';
    const dl = h('dl', { class: 'ps-dl' });
    const row = (k, ...v) => { dl.append(h('dt', { text: k }), h('dd', {}, ...v)); };
    row('날짜', h('span', { class: 'num', text: fmtYMD(p.date, true) || '—' }), h('span', { class: 'tag', text: p.date_source === 'name' ? '폴더 이름' : '최근 수정' , 'data-tip': p.date_source === 'name' ? '폴더 이름 앞의 날짜에서 읽었어요' : '폴더 이름에 날짜가 없어 안의 파일 중 가장 최근 수정 시각을 썼어요' }));
    row('광고주', h('span', { text: p.client || '미분류' }), p.client_raw && p.client_raw !== p.client ? h('span', { class: 'dim', text: `원래 표기 ${p.client_raw}` }) : null);
    row('작업자', p.worker_name ? h('span', { text: p.worker_name }) : null, p.worker ? h('span', { class: p.worker_name ? 'tag' : '', text: p.worker }) : h('span', { class: 'dim', text: '—' }));
    row('작업 종류', h('span', { class: p.job_kind ? '' : 'dim', text: p.job_kind || '—' }));
    row('위치', h('span', { text: p.drive_label || driveName(d) || p.drive }), kindTxt ? h('span', { class: 'tag', text: ext + kindTxt }) : null, p.connected ? null : h('span', { class: 'dim', text: '연결 안 됨' }));
    const pathSpan = h('span', {}, h('bdi', { text: p.path }));
    const rv = h('button', { class: 'ibtn xs', type: 'button', html: ic('reveal'), 'data-tip': '탐색기에서 열기', disabled: !p.connected });
    rv.addEventListener('click', () => (ctx.reveal ? ctx.reveal(p.path) : POST('/api/reveal', { path: p.path }).catch(e => toastErr(e))));
    dl.append(h('dt', { text: '경로' }), h('dd', { class: 'path', 'data-tip': p.path }, pathSpan, rv));
    row('크기', h('span', { class: 'num', text: fmtSize(p.size) }), h('span', { class: 'dim num', text: `파일 ${Number(p.files || 0).toLocaleString('ko-KR')}개 · 영상 ${Math.max(Number(p.videos || 0), (r && r.videos || []).length).toLocaleString('ko-KR')}개` }));
    if (p.newest) row('최근 수정', h('span', { class: 'num', text: fmtTs(p.newest) }), h('span', { class: 'dim', text: fmtAgo(p.newest) }));
    box.replaceChildren(h('h4', { text: '정보' }), dl);
  }
  function fillComp(box, p, children) {
    const t = p.size || 0;
    const lg = h('div', { class: 'ps-legend' });
    (p.cat_bytes || []).map((v, i) => [i, v || 0]).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]).forEach(([i, v]) => {
      lg.append(h('div', { style: { '--sc': catVar(i) } }, h('i'), h('span', { text: catName(i) }), h('b', { class: 'num', text: fmtSize(v) }), h('span', { class: 'num', style: { color: 'var(--text3)' }, text: t ? Math.round(v / t * 100) + '%' : '' })));
    });
    const kids = h('div', { class: 'ps-kids' });
    if (children === null) for (let i = 0; i < 3; i++) kids.append(h('div', { class: 'ps-sk', style: { height: '26px', margin: '3px 0' } }));
    else {
      const dirs = children.filter(c => c.kind === 'dir' || c.kind === 'file' || c.kind === 'seq' || c.kind === 'loose').slice(0, 8);
      const mx = Math.max(1, ...dirs.map(c => c.size || 0));
      for (const c of dirs) {
        const k = h('div', { class: 'ps-kid', 'data-tip': c.path || c.name },
          h('div', { class: 'n', html: ic(c.kind === 'dir' ? 'folder' : (PCAT_ICON[c.cat] || 'grid')) + `<span>${esc(c.kind === 'loose' ? (c.name || '나머지 파일') : c.name)}</span>` }),
          h('div', { class: 'v', text: fmtSize(c.size) }));
        const w = h('div', { style: { width: Math.max(2, (c.size || 0) / mx * 100) + '%' } }, miniBar(c.cat_bytes || (c.cat != null ? Object.assign(new Array(NCAT).fill(0), { [c.cat]: c.size }) : []), c.size, 'sm'));
        const mb = h('div', { class: 'pj-mb sm', style: { background: 'transparent' } });
        mb.append(w);
        k.append(mb);
        kids.append(k);
      }
      if (children.length > dirs.length) kids.append(h('div', { class: 'og-empty', style: { padding: '6px 0 0' }, text: `그 밖에 ${children.length - dirs.length}개` }));
    }
    box.replaceChildren(h('h4', {}, h('span', { text: '폴더 구성' }), h('span', { class: 'sp' }), h('b', { class: 'num', text: fmtSize(t) })),
      miniBar(p.cat_bytes, t, 'lg' + (reduced ? '' : ' grow')), lg, children && children.length ? h('h4', { style: { margin: '18px 0 4px' }, text: '안의 폴더' }) : null, kids);
  }

  /* ═════════════════════════════════════════ 광고주 정리 시트 */
  async function getSettings(fresh) {
    if (S.settingsCache && !fresh) return S.settingsCache;
    const r = await GET('/api/settings');
    S.settingsCache = r.settings || {};
    return S.settingsCache;
  }
  const ckey = s => String(s || '').normalize('NFKC').replace(/\s+/g, '').toLowerCase();
  async function saveSettings(part) {
    const r = await POST('/api/settings', part);
    S.settingsCache = r.settings || null;
    return r.settings;
  }
  function openOrganizer(tab) {
    if (sheet) sheet.close();
    const sd = Side({ cls: 'og', label: '광고주 정리', width: 620 });
    sheet = sd;
    sd.onClose = () => { if (sheet === sd) sheet = null; };
    sd.hd.append(h('div', { class: 'tt' }, h('h2', { text: '광고주 정리' }), h('p', { text: '같은 광고주가 여러 이름으로 나뉘어 있으면 합치고, 작업자 이니셜에 이름을 붙여요.' })), sd.x);
    const TABS = [{ id: 'clients', label: '광고주' }, { id: 'workers', label: '작업자' }, { id: 'folders', label: '프로젝트 폴더' }];
    let cur = tab || 'clients';
    const tb = Seg(TABS, cur, id => { cur = id; draw(); }, 'og-tabs sm');
    const pane = h('div', { class: 'og-pane' });
    sd.bd.append(tb.el, pane);
    let clients = [], workers = [], settings = null, selA = [], keepIdx = 0, editing = null;

    async function fetchAll() {
      try {
        const [r, s] = await Promise.all([GET('/api/projects', { group: 'client' }), getSettings(true)]);
        clients = r.clients || []; workers = r.workers || []; settings = s;
      } catch (e) { toastErr(e, '목록을 불러오지 못했어요'); }
    }
    function draw() {
      sd.ft.replaceChildren();
      if (cur === 'clients') drawClients();
      else if (cur === 'workers') drawWorkers();
      else drawFolders();
    }
    function drawClients() {
      const mx = Math.max(1, ...clients.map(c => c.bytes));
      const lst = h('div', { class: 'og-list' });
      for (const c of clients) {
        const unk = c.name === '미분류';
        const isSel = selA.includes(c.name);
        const row = h('div', { class: 'og-row' + (isSel ? ' sel' : '') + (unk ? ' unk' : '') + (editing === c.name ? ' edit' : ''), role: unk ? null : 'button', tabindex: unk ? null : '0', 'data-name': c.name });
        const aliasesOf = Object.entries((settings && settings.client_aliases) || {}).filter(([, v]) => ckey(v) === ckey(c.name)).map(([a]) => a);
        if (editing === c.name) {
          const inp = h('input', { class: 'tin', type: 'text', value: c.name, spellcheck: 'false', 'aria-label': '새 이름', 'data-esc-close': '' });
          const ok = h('button', { class: 'btn sm primary', type: 'button', text: '저장' });
          const no = h('button', { class: 'ibtn sm', type: 'button', html: ic('x'), 'data-tip': '취소' });
          row.append(h('span'), h('div', { style: { gridColumn: '2 / span 2' } }, inp), ok, no);
          const save = () => rename(c.name, inp.value);
          ok.addEventListener('click', save);
          no.addEventListener('click', () => { editing = null; draw(); });
          inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); editing = null; draw(); } });
          requestAnimationFrame(() => { inp.focus(); inp.select(); });
        } else {
          const ck = h('span', { class: 'ck' + (isSel ? ' on' : ''), html: ic('check') });
          const nm = h('div', { class: 'n' }, h('b', { text: c.name }), h('span', { class: 'num', text: c.count + '개' }), aliasesOf.length ? h('em', { text: `별칭 ${aliasesOf.length}`, 'data-tip': '이 이름으로 합친 표기: ' + aliasesOf.join(', ') }) : null);
          const meter = h('div', { class: 'meter' }, h('i', { style: { width: Math.max(2, c.bytes / mx * 100) + '%' } }));
          const ed = h('button', { class: 'ibtn xs', type: 'button', html: ic('edit'), 'data-tip': '이름 바꾸기', disabled: unk });
          row.append(ck, nm, meter, h('span', { class: 'v', text: fmtSize(c.bytes) }), unk ? h('span') : ed);
          ed.addEventListener('click', e => { e.stopPropagation(); editing = c.name; draw(); });
          if (!unk) {
            const tog = () => {
              const i = selA.indexOf(c.name);
              if (i >= 0) selA.splice(i, 1); else { selA.push(c.name); if (selA.length > 2) selA.shift(); }
              keepIdx = selA.length === 2 ? (bytesOf(selA[0]) >= bytesOf(selA[1]) ? 0 : 1) : 0;
              draw();
            };
            row.addEventListener('click', tog);
            row.addEventListener('keydown', e => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); tog(); } });
            row.addEventListener('dblclick', e => { e.preventDefault(); editing = c.name; draw(); });
          }
        }
        lst.append(row);
      }
      const hint = h('p', { class: 'og-hint', text: clients.length ? '두 광고주를 고르면 하나로 합칠 수 있어요. 이름을 두 번 누르면 이름을 바꿔요.' : '아직 감지된 광고주가 없어요.' });
      const aliases = Object.entries((settings && settings.client_aliases) || {});
      const al = h('div', {});
      if (aliases.length) {
        al.append(h('div', { class: 'og-sub' }, h('span', { text: `저장된 별칭 ${aliases.length}개` }), h('span', { class: 'sp' })));
        for (const [a, c] of aliases.sort((x, y) => x[1].localeCompare(y[1], 'ko') || x[0].localeCompare(y[0], 'ko'))) {
          const x = h('button', { class: 'ibtn xs danger', type: 'button', html: ic('x'), 'data-tip': '이 별칭을 지워요' });
          x.addEventListener('click', () => removeAlias(a));
          al.append(h('div', { class: 'og-al' }, h('span', { text: a }), h('span', { html: ic('arrowR') }), h('span', { class: 'c', text: c }), x));
        }
      }
      pane.replaceChildren(hint, lst, al);
      // 바닥
      if (selA.length === 2) {
        const seg = Seg(selA.map((n, i) => ({ id: String(i), label: n })), String(keepIdx), id => { keepIdx = +id; drawMergeText(); }, 'sm');
        const l1 = h('div', { class: 'l1' });
        const drawMergeText = () => {
          const keep = selA[keepIdx], gone = selA[1 - keepIdx];
          l1.innerHTML = `<b>${esc(gone)}</b> 의 프로젝트 ${countOf(gone)}개를 <b>${esc(keep)}</b> 로 합쳐요.`;
        };
        drawMergeText();
        const go = h('button', { class: 'btn primary', type: 'button', html: ic('merge') + '<span>합치기</span>' });
        go.addEventListener('click', () => merge(selA[1 - keepIdx], selA[keepIdx], go));
        const cancel = h('button', { class: 'btn ghost', type: 'button', text: '선택 해제', onclick: () => { selA = []; draw(); } });
        sd.ft.append(h('div', { class: 'og-merge' }, l1, h('div', { class: 'l2' }, h('span', { class: 'lb', text: '남길 이름' }), seg.el, h('span', { class: 'sp', style: { flex: '1' } }), cancel, go)));
      } else if (selA.length === 1) {
        sd.ft.append(h('span', { class: 'og-hint', style: { margin: '0' }, html: `<b style="color:var(--text)">${esc(selA[0])}</b> 와 합칠 광고주를 하나 더 골라 주세요.` }), h('div', { class: 'sp' }),
          h('button', { class: 'btn ghost', type: 'button', text: '이름 바꾸기', onclick: () => { editing = selA[0]; selA = []; draw(); } }));
      } else {
        sd.ft.append(h('span', { class: 'og-hint', style: { margin: '0' }, text: `광고주 ${clients.filter(c => c.name !== '미분류').length}곳` }), h('div', { class: 'sp' }),
          h('button', { class: 'btn', type: 'button', text: '닫기', onclick: () => sd.close() }));
      }
    }
    const bytesOf = n => (clients.find(c => c.name === n) || {}).bytes || 0;
    const countOf = n => (clients.find(c => c.name === n) || {}).count || 0;
    function aliasMapWith(from, to) {
      const cur0 = Object.assign({}, (settings && settings.client_aliases) || {});
      const out = {};
      for (const [a, c] of Object.entries(cur0)) {
        if (ckey(a) === ckey(to)) continue;                       // 사슬이 거꾸로 돌지 않게
        out[a] = ckey(c) === ckey(from) ? to : c;                 // from 으로 가던 별칭도 to 로
      }
      if (ckey(from) !== ckey(to)) out[from] = to;
      return { prev: cur0, next: out };
    }
    async function applyAliases(next, prev, msg, sub) {
      await saveSettings({ client_aliases: next });
      settings = S.settingsCache;
      await fetchAll();
      draw();
      load({ quiet: true });
      toast(msg, {
        kind: 'ok', sub,
        actions: [{ label: '되돌리기', primary: true, onClick: async () => {
          try { await saveSettings({ client_aliases: prev }); settings = S.settingsCache; await fetchAll(); if (sheet === sd) draw(); load({ quiet: true }); }
          catch (e) { toastErr(e, '되돌리지 못했어요'); }
        } }],
      });
    }
    async function merge(from, to, btn) {
      btn && (btn.disabled = true);
      const { prev, next } = aliasMapWith(from, to);
      try {
        selA = [];
        await applyAliases(next, prev, '광고주를 합쳤어요', `${from} → ${to}`);
        flashRow(to);
      } catch (e) { btn && (btn.disabled = false); toastErr(e, '합치지 못했어요'); }
    }
    async function rename(from, to) {
      to = String(to || '').trim();
      if (!to || to === from) { editing = null; draw(); return; }
      if (to === '미분류') { toast('「미분류」는 쓸 수 없는 이름이에요', { kind: 'err' }); return; }
      const { prev, next } = aliasMapWith(from, to);
      try {
        editing = null;
        await applyAliases(next, prev, '광고주 이름을 바꿨어요', `${from} → ${to}`);
        flashRow(to);
      } catch (e) { toastErr(e, '이름을 바꾸지 못했어요'); }
    }
    async function removeAlias(a) {
      const prev = Object.assign({}, (settings && settings.client_aliases) || {});
      const next = Object.assign({}, prev);
      delete next[a];
      try { await applyAliases(next, prev, '별칭을 지웠어요', a); } catch (e) { toastErr(e, '별칭을 지우지 못했어요'); }
    }
    function flashRow(name) {
      requestAnimationFrame(() => {
        const r = [...pane.querySelectorAll('.og-row')].find(x => x.dataset.name === name);
        if (r) { r.classList.add('flash'); r.scrollIntoView({ block: 'nearest' }); setTimeout(() => r.classList.remove('flash'), 1000); }
      });
    }
    function drawWorkers() {
      const names = Object.assign({}, (settings && settings.workers) || {});
      const rows = new Map();
      for (const w of workers) rows.set(w.initial, { initial: w.initial, count: w.count });
      for (const k of Object.keys(names)) if (!rows.has(k.toUpperCase())) rows.set(k.toUpperCase(), { initial: k.toUpperCase(), count: 0 });
      const draft = {};
      const lst = h('div', { class: 'og-list' });
      for (const w of rows.values()) {
        const inp = h('input', { class: 'tin', type: 'text', value: names[w.initial] || '', placeholder: '이름 (예: 홍길동)', spellcheck: 'false', 'aria-label': `${w.initial} 의 이름`, maxlength: '40' });
        inp.addEventListener('input', () => { draft[w.initial] = inp.value; dirty(); });
        inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); saveW(); } });
        const x = h('button', { class: 'ibtn xs danger', type: 'button', html: ic('x'), 'data-tip': '이름 지우기' });
        x.addEventListener('click', () => { inp.value = ''; draft[w.initial] = ''; dirty(); });
        lst.append(h('div', { class: 'og-w' }, h('span', { class: 'ini', text: w.initial }), inp, h('span', { class: 'c', text: w.count ? `${w.count}개` : '—' }), x));
      }
      if (!rows.size) lst.append(h('div', { class: 'og-empty', text: '폴더 이름에서 작업자 이니셜을 아직 찾지 못했어요.' }));
      const ni = h('input', { class: 'tin', type: 'text', placeholder: '이니셜', maxlength: '16', spellcheck: 'false', 'aria-label': '새 이니셜' });
      const nn = h('input', { class: 'tin', type: 'text', placeholder: '이름', maxlength: '40', spellcheck: 'false', 'aria-label': '새 이름' });
      const nb = h('button', { class: 'btn', type: 'button', html: ic('plus') + '<span>추가</span>' });
      const add = () => {
        const i = ni.value.trim().toUpperCase(), n = nn.value.trim();
        if (!i || !/^[A-Z0-9가-힣]{1,16}$/.test(i)) { ni.focus(); toast('이니셜을 영문·숫자로 입력해 주세요', { kind: 'err' }); return; }
        if (!n) { nn.focus(); return; }
        draft[i] = n;
        saveW();
      };
      nb.addEventListener('click', add);
      nn.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); add(); } });
      pane.replaceChildren(h('p', { class: 'og-hint', text: '폴더 이름 끝의 작업자 이니셜(KMS 등)에 이름을 붙이면 카드와 필터에 이름으로 보여요. 이 설정은 이 PC 에만 저장돼요.' }), lst,
        h('div', { class: 'og-sub' }, h('span', { text: '작업자 추가' })), h('div', { class: 'og-add' }, ni, nn, nb));
      const saveBtn = h('button', { class: 'btn primary', type: 'button', text: '저장', disabled: true });
      const st = h('span', { class: 'og-hint', style: { margin: '0' }, text: '' });
      saveBtn.addEventListener('click', () => saveW());
      sd.ft.append(st, h('div', { class: 'sp' }), saveBtn);
      function dirty() {
        const ch = Object.keys(draft).some(k => (draft[k] || '') !== (names[k] || ''));
        saveBtn.disabled = !ch;
        st.textContent = ch ? '바꾼 내용이 있어요' : '';
      }
      async function saveW() {
        const next = Object.assign({}, names);
        for (const [k, v] of Object.entries(draft)) { if (v && v.trim()) next[k] = v.trim(); else delete next[k]; }
        saveBtn.disabled = true;
        try {
          await saveSettings({ workers: next });
          settings = S.settingsCache;
          await fetchAll();
          draw();
          load({ quiet: true });
          toast('작업자 이름을 저장했어요', { kind: 'ok' });
        } catch (e) { saveBtn.disabled = false; toastErr(e, '저장하지 못했어요'); }
      }
    }
    function drawFolders() {
      const roots = (settings && settings.project_roots) || [];
      const excl = (settings && settings.project_excluded) || [];
      const lst = h('div', {});
      if (!roots.length) lst.append(h('div', { class: 'og-empty', text: '지정한 폴더가 없어요. 날짜로 시작하거나 aep·prproj·drp 가 든 폴더는 지정하지 않아도 찾아요.' }));
      for (const r of roots) {
        const x = h('button', { class: 'ibtn xs danger', type: 'button', html: ic('x'), 'data-tip': '목록에서 빼기' });
        x.addEventListener('click', async () => { try { await POST('/api/projects/roots', { remove: r }); S.settingsCache = null; settings = await getSettings(true); draw(); load({ quiet: true }); } catch (e) { toastErr(e, '빼지 못했어요'); } });
        lst.append(h('div', { class: 'og-folder' }, h('span', { class: 'tile t-sm', 'data-c': 'drive', html: ic('folder') }), h('span', { class: 'p', 'data-tip': r }, h('bdi', { text: r })), x));
      }
      const inp = h('input', { class: 'tin', type: 'text', placeholder: '예: D:\\Projects', spellcheck: 'false', 'aria-label': '프로젝트 폴더 경로' });
      const pick = h('button', { class: 'btn', type: 'button', html: ic('folder') + '<span>폴더 선택…</span>' });
      const add = h('button', { class: 'btn', type: 'button', text: '추가' });
      const submit = async v => {
        v = String(v || '').trim();
        if (!v) { inp.focus(); return; }
        try { await POST('/api/projects/roots', { add: v }); S.settingsCache = null; settings = await getSettings(true); draw(); load({ quiet: true }); toast('프로젝트 폴더를 추가했어요', { kind: 'ok', sub: v }); }
        catch (e) { toastErr(e, '폴더를 추가하지 못했어요'); }
      };
      pick.addEventListener('click', async () => { const p = await pickFolder(); if (p === false) { inp.focus(); toast('경로를 직접 입력해 주세요', { kind: 'info' }); } else if (p) submit(p); });
      add.addEventListener('click', () => submit(inp.value));
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); submit(inp.value); } });
      const exl = h('div', {});
      if (excl.length) {
        exl.append(h('div', { class: 'og-sub' }, h('span', { text: `프로젝트가 아니라고 표시한 폴더 ${excl.length}개` })));
        for (const r of excl) {
          const u = h('button', { class: 'btn sm ghost', type: 'button', html: ic('undo') + '<span>되돌리기</span>' });
          u.addEventListener('click', async () => { try { await POST('/api/projects/not_project', { path: r, remove: true }); S.settingsCache = null; settings = await getSettings(true); draw(); load({ quiet: true }); } catch (e) { toastErr(e, '되돌리지 못했어요'); } });
          exl.append(h('div', { class: 'og-folder' }, h('span', { class: 'tile t-sm', 'data-c': 'un', html: ic('notp') }), h('span', { class: 'p', 'data-tip': r }, h('bdi', { text: r })), u));
        }
      }
      pane.replaceChildren(h('p', { class: 'og-hint', text: '여기 지정한 폴더의 바로 아래 폴더들을 프로젝트로 봐요.' }), lst,
        h('div', { class: 'og-sub' }, h('span', { text: '폴더 추가' })), h('div', { class: 'addrow', style: { gridTemplateColumns: 'minmax(0,1fr) auto auto' } }, inp, pick, add), exl);
      sd.ft.append(h('div', { class: 'sp' }), h('button', { class: 'btn', type: 'button', text: '닫기', onclick: () => sd.close() }));
    }
    pane.append(h('div', { class: 'ps-sk', style: { height: '200px' } }));
    fetchAll().then(() => { if (sheet === sd) draw(); });
  }

  /* ═════════════════════════════════════════ 상태 연동 */
  function onState(st) {
    if (!alive || !st) return;
    S.lastState = performance.now();
    const pv = st.previews;
    if (pv && pv.done) { Posters.onDone(pv.done); fireDone(pv.done); }
    // 스캔이 끝났거나 작업(이동·복사·휴지통·되돌리기)이 끝나면 목록을 새로
    const ds = (st.drives || []).map(d => `${d.letter}:${d.scanned_at || 0}:${d.connected === false ? 0 : 1}`).join(',');
    const js = (st.jobs || []).filter(j => j && /^(done|partial|failed|canceled)$/.test(j.status) && j.type !== 'verify_dupes')
      .map(j => j.id + j.status).join(',');
    if (S.lastSig && (S.lastSig !== ds || S.jobSig !== js)) {
      clearTimeout(S.refT);
      S.refT = setTimeout(() => { if (alive) load({ quiet: true }); }, 400);
    }
    S.lastSig = ds; S.jobSig = js;
  }

  // 셸이 상태를 안 넘겨 주는 동안(단독 시험 등)에는 대기 중인 미리보기만 스스로 묻는다(Posters 의 타이머).
  load({ anim: true });

  function onKey(e) {
    if (!alive || topSide || inField(e) || e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.key === '/') { e.preventDefault(); qIn.focus(); qIn.select(); return true; }
    return false;
  }

  const api_ = {
    onKey,
    unmount() {
      if (!alive) return;
      alive = false;
      clearTimeout(S.refT); clearTimeout(qT); clearTimeout(S.loadT);
      for (const d of disposers.splice(0).reverse()) { try { d(); } catch (e) { /* 무시 */ } }
      for (const c of S.cards.values()) c.hide();
      S.cards.clear();
      root.remove();
    },
    refresh() { if (alive) load({ quiet: true }); },
    onState,
    // 시험·셸 보조용
    get state() { return { group: S.group, total: S.data ? S.data.total : null, selected: [...S.sel.keys()] }; },
    openOrganizer: t => openOrganizer(t),
  };
  return api_;
}

let current = null;
window.Projects = {
  mount(containerEl, ctx) {
    if (!containerEl) throw new Error('Projects.mount: container 가 필요해요');
    const inst = create(containerEl, ctx);
    current = inst;
    const un = inst.unmount;
    inst.unmount = () => { if (current === inst) current = null; un(); };
    return inst;
  },
  /* 설정 화면 등에서 「광고주 정리」를 바로 열 때(붙어 있는 화면이 있어야 한다) */
  openOrganizer(tab) { if (current) current.openOrganizer(tab); },
};
})();
