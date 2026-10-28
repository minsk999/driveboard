/* DriveBoard UI — vanilla JS, 프레임워크·외부 요청 없음. 계약: SPEC §7 (/api/*), 토큰은 <meta name="db-token">. */
'use strict';
(() => {

/* ═════════════════════════════════════════════ 기본 도구 */
const TOKEN = (document.querySelector('meta[name="db-token"]') || {}).content || '';
const RMQ = window.matchMedia ? matchMedia('(prefers-reduced-motion: reduce)') : null;
let reduced = !!(RMQ && RMQ.matches);
if (RMQ && RMQ.addEventListener) RMQ.addEventListener('change', e => { reduced = e.matches; });

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const NCAT = 11;
const GB = 1024 ** 3;
const MARGIN = GB;

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
  if (tag === 'button' && a && a['data-tip'] && !a['aria-label'] && !a.text) el.setAttribute('aria-label', a['data-tip']);
  return el;
}
/* 같은 내용이면 DOM 을 건드리지 않는다(폴링마다 깜빡임·호버 끊김 방지) */
function setH(el, html) { if (el._h !== html) { el._h = html; el.innerHTML = html; } }
function setT(el, t) { if (el.textContent !== t) el.textContent = t; }
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const sum = arr => arr.reduce((a, b) => a + (b || 0), 0);
const pk = p => String(p || '').replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase();
const raf2 = fn => requestAnimationFrame(() => requestAnimationFrame(fn));
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* 크기: 1024 진법, 유효숫자 3자리(버림), 탐색기 표기 */
function fmtBytes(n) {
  n = Math.floor(Number(n) || 0);
  if (n <= 0) return '0KB';
  const U = ['KB', 'MB', 'GB', 'TB', 'PB'];
  let v = n / 1024, u = 0;
  while (v >= 1000 && u < U.length - 1) { v /= 1024; u++; }
  if (u === 0 && v < 1) return Math.max(0.1, Math.floor(v * 10 + 1e-9) / 10).toFixed(1) + 'KB';
  let s;
  if (v < 10) s = (Math.floor(v * 100 + 1e-9) / 100).toFixed(2);
  else if (v < 100) s = (Math.floor(v * 10 + 1e-9) / 10).toFixed(1);
  else s = String(Math.floor(v + 1e-9));
  return s + U[u];
}
const fmtNum = n => Math.round(Number(n) || 0).toLocaleString('ko-KR');
const fmtPct = (a, b) => {
  if (!b) return '0%';
  const p = a / b * 100;
  if (p > 0 && p < 0.1) return '0.1% 미만';
  return (p < 10 ? p.toFixed(1) : Math.round(p)) + '%';
};
function fmtDur(s) {
  s = Math.max(0, Math.round(Number(s) || 0));
  if (s < 60) return s + '초';
  if (s < 3600) { const m = Math.floor(s / 60), r = s % 60; return r && m < 10 ? `${m}분 ${r}초` : `${m}분`; }
  const hh = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return m ? `${hh}시간 ${m}분` : `${hh}시간`;
}
const nowS = () => Date.now() / 1000 + S.skew;
function relTime(ts) {
  if (!ts) return '—';
  const d = nowS() - ts;
  if (d < 60) return '방금';
  if (d < 3600) return Math.floor(d / 60) + '분 전';
  if (d < 86400) return Math.floor(d / 3600) + '시간 전';
  if (d < 172800) return '어제';
  const days = Math.floor(d / 86400);
  if (days < 7) return days + '일 전';
  if (days < 30) return Math.floor(days / 7) + '주 전';
  if (days < 365) return Math.max(1, Math.floor(days / 30)) + '개월 전';
  return Math.floor(days / 365) + '년 전';
}
function absTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}. ${d.getMonth() + 1}. ${d.getDate()}. ${p(d.getHours())}:${p(d.getMinutes())}`;
}
const AGE_LABEL = { 90: '3개월', 180: '6개월', 365: '1년' };
const ageText = a => AGE_LABEL[a] || (a >= 365 && a % 365 === 0 ? a / 365 + '년' : a >= 30 && a % 30 === 0 ? a / 30 + '개월' : a + '일');

/* 숫자 트윈 — 값이 바뀌면 부드럽게 따라간다 */
function tw(el, to, fmt, dur = 700) {
  if (!el) return;
  to = Number(to) || 0;
  if (el._tvSet && el._tvTo === to) return;
  if (!el._tvSet) {
    // 화면을 처음 볼 때만 0 에서 카운트업, 다시 오면 바로 값
    const scr = el.closest ? el.closest('.screen') : null;
    const intro = scr ? scr._intro !== false : S.introNow !== false;
    el._tv = intro ? 0 : to;
  }
  const from = el._tv == null ? to : el._tv;
  el._tvSet = true;
  el._tvTo = to;
  cancelAnimationFrame(el._raf);
  if (reduced || !el.isConnected || from === to || document.visibilityState === 'hidden') {
    el._tv = to; el.textContent = fmt(to); return;
  }
  const t0 = performance.now();
  const step = t => {
    const p = Math.min(1, (t - t0) / dur);
    const e = 1 - Math.pow(1 - p, 3);
    el._tv = from + (to - from) * e;
    el.textContent = fmt(p >= 1 ? to : el._tv);
    if (p < 1) el._raf = requestAnimationFrame(step);
  };
  el._raf = requestAnimationFrame(step);
}

/* ═════════════════════════════════════════════ 아이콘(인라인 SVG) */
const P = {
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  chevR: '<path d="M9.5 6l6 6-6 6"/>',
  chevL: '<path d="M14.5 6l-6 6 6 6"/>',
  chevD: '<path d="M6.5 9.5l5.5 5.5 5.5-5.5"/>',
  back: '<path d="M14.5 6l-6 6 6 6"/>',
  gear: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  scan: '<path d="M19.5 11a7.5 7.5 0 0 0-13.7-3.6L4.5 9"/><path d="M4.5 4.5V9H9"/><path d="M4.5 13a7.5 7.5 0 0 0 13.7 3.6l1.3-1.6"/><path d="M19.5 19.5V15H15"/>',
  trash: '<path d="M4.5 7h15"/><path d="M9.5 7V5.3c0-.7.5-1.3 1.2-1.3h2.6c.7 0 1.2.6 1.2 1.3V7"/><path d="M6.6 7l.8 11.1A2 2 0 0 0 9.4 20h5.2a2 2 0 0 0 2-1.9L17.4 7"/><path d="M10.2 11v5M13.8 11v5"/>',
  move: '<path d="M3.5 12h11"/><path d="M11 8l4 4-4 4"/><path d="M14.5 4.5h3a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-3"/>',
  folder: '<path d="M3.5 7.4c0-1.1.9-2 2-2h3.7l2 2.1h7.3c1.1 0 2 .9 2 2v7.6c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2z"/>',
  reveal: '<path d="M3.5 7.4c0-1.1.9-2 2-2h3.7l2 2.1h7.3c1.1 0 2 .9 2 2v7.6c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2z"/><path d="M10 13.5h5M12.8 11.2l2.3 2.3-2.3 2.3"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.2"/><path d="M15.5 8.5V6.2a1.7 1.7 0 0 0-1.7-1.7H6.2a1.7 1.7 0 0 0-1.7 1.7v7.6c0 .9.8 1.7 1.7 1.7h2.3"/>',
  tag: '<path d="M3.5 12.3V4.8c0-.7.6-1.3 1.3-1.3h7.5l8.2 8.2c.5.5.5 1.3 0 1.8l-6.7 6.7c-.5.5-1.3.5-1.8 0z"/><circle cx="8" cy="8" r="1.4"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  lock: '<rect x="5.5" y="10.5" width="13" height="9.5" rx="2.2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><path d="M12 7.6h.01"/>',
  warn: '<path d="M10.3 4.6L3.4 16.8A2 2 0 0 0 5.1 19.8h13.8a2 2 0 0 0 1.7-3L13.7 4.6a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4"/><path d="M12 16.6h.01"/>',
  sort: '<path d="M8 5v14M4.5 15.5L8 19l3.5-3.5"/><path d="M16 19V5M12.5 8.5L16 5l3.5 3.5"/>',
  down: '<path d="M12 5v13.5M6.5 13l5.5 5.5 5.5-5.5"/>',
  up: '<path d="M12 19V5.5M6.5 11L12 5.5l5.5 5.5"/>',
  minus: '<path d="M6 12h12"/>',
  plus: '<path d="M12 6v12M6 12h12"/>',
  arrowR: '<path d="M5 12h13.5M13 6.5l5.5 5.5-5.5 5.5"/>',
  ssd: '<rect x="3.5" y="6" width="17" height="12" rx="2.6"/><path d="M7 10h4.5M7 14h2.5"/><circle cx="16.5" cy="12" r="1.3"/>',
  hdd: '<rect x="4.5" y="3.5" width="15" height="17" rx="2.6"/><circle cx="12" cy="11" r="4.2"/><circle cx="12" cy="11" r=".9"/><path d="M8 17.3h2.5"/>',
  usb: '<rect x="7" y="9.5" width="10" height="11" rx="2"/><path d="M9 9.5V4h6v5.5"/><path d="M10.6 6.3h.01M13.4 6.3h.01"/>',
  net: '<rect x="3.5" y="4" width="17" height="10" rx="2.3"/><path d="M12 14v3.5M5 19.5h14"/><path d="M7 9h3"/>',
  all: '<rect x="3.5" y="4" width="17" height="6.5" rx="2.1"/><rect x="3.5" y="13.5" width="17" height="6.5" rx="2.1"/><path d="M7 7.25h3M7 16.75h3"/>',
  dupes: '<rect x="8" y="8" width="11.5" height="11.5" rx="2.4"/><path d="M15.5 8V6.4A1.9 1.9 0 0 0 13.6 4.5H6.4a1.9 1.9 0 0 0-1.9 1.9v7.2c0 1 .9 1.9 1.9 1.9H8"/>',
  big: '<path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z"/><path d="M14 3.5V8h4.5"/><path d="M12 11v6M9.5 14.5L12 17l2.5-2.5"/>',
  oldf: '<path d="M3.5 7.4c0-1.1.9-2 2-2h3.7l2 2.1h7.3c1.1 0 2 .9 2 2v7.6c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2z"/><circle cx="12" cy="13.6" r="3.1"/><path d="M12 12.2v1.5l1 .7"/>',
  spark: '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1-5.1-1.9 5.1-1.9z"/><path d="M18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
  spinner: '<path d="M12 3.5a8.5 8.5 0 1 1-8.5 8.5" />',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2.2"/>',
  list: '<path d="M8.5 7h11M8.5 12h11M8.5 17h11"/><path d="M4.5 7h.01M4.5 12h.01M4.5 17h.01"/>',
  offline: '<path d="M4 4l16 16"/><path d="M8.5 8.6A6.5 6.5 0 0 0 5.6 11.8"/><path d="M12 7.5c2.8 0 5.2 1.6 6.4 4"/><path d="M9.7 14.4a3.2 3.2 0 0 1 4.6 0"/><path d="M12 18h.01"/>',
  /* 카테고리 */
  c0: '<path d="M13.2 3.5L6 13.4h5.6L10.8 20.5 18 10.6h-5.6z"/>',
  c1: '<path d="M12 14.5V4.5"/><path d="M7.8 8.7L12 4.5l4.2 4.2"/><path d="M5 13v4.3A2.7 2.7 0 0 0 7.7 20h8.6a2.7 2.7 0 0 0 2.7-2.7V13"/>',
  c2: '<path d="M4 8.8a2.2 2.2 0 0 1 2.2-2.2h1.7l1.4-2.1h5.4l1.4 2.1h1.7A2.2 2.2 0 0 1 20 8.8v8a2.2 2.2 0 0 1-2.2 2.2H6.2A2.2 2.2 0 0 1 4 16.8z"/><circle cx="12" cy="12.6" r="3.3"/>',
  c3: '<rect x="8" y="8" width="12" height="9.5" rx="2"/><path d="M4.5 13.5V6.4c0-1 .8-1.9 1.9-1.9H15.5"/>',
  c4: '<rect x="3.5" y="5.5" width="17" height="13" rx="2.6"/><path d="M10.4 9.4v5.2l4.3-2.6z"/>',
  c5: '<path d="M12 4l8.5 4.4L12 12.8 3.5 8.4z"/><path d="M3.5 12.4L12 16.8l8.5-4.4"/><path d="M3.5 16.1L12 20.5l8.5-4.4"/>',
  c6: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.6"/><circle cx="9" cy="9.6" r="1.6"/><path d="M20.5 15.6L16 11.1l-8.4 8.4"/>',
  c7: '<path d="M4.5 10.2v3.6M8.2 7.2v9.6M12 4.5v15M15.8 8v8M19.5 10.5v3"/>',
  c8: '<rect x="3.5" y="4.5" width="17" height="4.6" rx="1.6"/><path d="M5 9.1v8.7A2.2 2.2 0 0 0 7.2 20h9.6a2.2 2.2 0 0 0 2.2-2.2V9.1"/><path d="M10 13h4"/>',
  c9: '<rect x="4" y="4" width="6.6" height="6.6" rx="1.7"/><rect x="13.4" y="4" width="6.6" height="6.6" rx="1.7"/><rect x="4" y="13.4" width="6.6" height="6.6" rx="1.7"/><rect x="13.4" y="13.4" width="6.6" height="6.6" rx="1.7"/>',
  c10: '<circle cx="6.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="17.5" cy="12" r="1.2"/>',
  bin: '<path d="M4.5 7h15"/><path d="M9.5 7V5.3c0-.7.5-1.3 1.2-1.3h2.6c.7 0 1.2.6 1.2 1.3V7"/><path d="M6.6 7l.8 11.1A2 2 0 0 0 9.4 20h5.2a2 2 0 0 0 2-1.9L17.4 7"/>',
  un: '<circle cx="12" cy="12" r="8.5"/><path d="M9.7 9.5a2.4 2.4 0 0 1 4.6.9c0 1.6-2.3 2-2.3 3.4"/><path d="M12 16.8h.01"/>',
  seq: '<rect x="3.5" y="6" width="12" height="12" rx="2"/><path d="M18 8v8M20.5 9.5v5"/>',
  /* 장치 — 같은 선 굵기·모서리의 한 가족 */
  ext: '<rect x="3.5" y="4.5" width="12" height="15" rx="2.6"/><path d="M7 8.5h5"/><circle cx="9.5" cy="15" r="1.1"/><path d="M15.5 13.5h1.8a2.2 2.2 0 0 1 2.2 2.2v4.8"/>',
  nas: '<rect x="4" y="3.5" width="16" height="7.5" rx="2.1"/><rect x="4" y="13" width="16" height="7.5" rx="2.1"/><path d="M11.5 7.25h5M11.5 16.75h5"/><path d="M7.5 7.25h.01M7.5 16.75h.01"/>',
  storage: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5V12l6.1 5.9"/>',
  proj: '<rect x="3.5" y="7.5" width="17" height="12" rx="2.4"/><path d="M3.8 7.6l15.5-3.4"/><path d="M8.3 6.6l1.6 3.4M13 5.6l1.6 3.4"/><path d="M10.3 11.6v4.8l4-2.4z"/>',
  unplug: '<path d="M9 4.5v4M15 4.5v4"/><path d="M6.5 8.5h11v2.8a5.5 5.5 0 0 1-5.5 5.5 5.5 5.5 0 0 1-5.5-5.5z"/><path d="M12 16.8v2.7"/><path d="M4 4l16 16"/>',
  export: '<path d="M12 14.5V4.5"/><path d="M8 8.3l4-3.8 4 3.8"/><path d="M5 13.5v3.8A2.7 2.7 0 0 0 7.7 20h8.6a2.7 2.7 0 0 0 2.7-2.7v-3.8"/>',
  import: '<path d="M12 4.5v10"/><path d="M8 10.7l4 3.8 4-3.8"/><path d="M5 13.5v3.8A2.7 2.7 0 0 0 7.7 20h8.6a2.7 2.7 0 0 0 2.7-2.7v-3.8"/>',
  people: '<circle cx="9" cy="8.5" r="3.2"/><path d="M3.5 19.5a5.5 5.5 0 0 1 11 0"/><path d="M15.2 5.6a3.2 3.2 0 0 1 0 5.8"/><path d="M17.6 14.4a5.5 5.5 0 0 1 2.9 5.1"/>',
  bolt: '<path d="M13.2 3.5L6 13.4h5.6L10.8 20.5 18 10.6h-5.6z"/>',
  ext2: '<path d="M14 4.5h5.5V10"/><path d="M19.5 4.5L11 13"/><path d="M17 13.5v4A2.5 2.5 0 0 1 14.5 20h-8A2.5 2.5 0 0 1 4 17.5v-8A2.5 2.5 0 0 1 6.5 7h4"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z"/>',
  more: '<circle cx="6.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="17.5" cy="12" r="1.2"/>',
  folderPlus: '<path d="M3.5 7.4c0-1.1.9-2 2-2h3.7l2 2.1h7.3c1.1 0 2 .9 2 2v7.6c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2z"/><path d="M12 11v5M9.5 13.5h5"/>',
};
function ic(name, cls) {
  const p = P[name] || P.c10;
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"${cls ? ` class="${cls}"` : ''} aria-hidden="true">${p}</svg>`;
}
const icEl = (name, cls) => { const t = document.createElement('template'); t.innerHTML = ic(name, cls); return t.content.firstChild; };
/* 장치 종류: 내장 SSD/HDD · 외장 디스크 · USB 메모리 · NAS */
function driveIcon(d) {
  if (!d) return 'ssd';
  if (d.kind === 'network') return 'nas';
  if (d.kind === 'usb' || d.kind === 'removable') return 'usb';
  if (d.external) return 'ext';
  return d.kind === 'hdd' ? 'hdd' : 'ssd';
}
const driveGroup = d => (!d ? 'int' : d.kind === 'network' ? 'net' : (d.external || d.kind === 'usb' || d.kind === 'removable') ? 'ext' : 'int');
const GROUPS = [{ id: 'int', label: '내장' }, { id: 'ext', label: '외장·USB' }, { id: 'net', label: '네트워크(NAS)' }];
function kindLabel(d) {
  if (!d) return '';
  if (d.kind === 'network') return 'NAS';
  if (d.kind === 'usb' || d.kind === 'removable') return 'USB';
  const k = d.kind === 'hdd' ? 'HDD' : d.kind === 'ssd' ? 'SSD' : '';
  return d.external ? (k ? '외장 ' + k : '외장') : k;
}
const isConn = d => !!d && d.connected !== false;
const hasBin = d => !!d && d.has_bin !== false;
const isLetterKey = k => /^[A-Za-z]$/.test(String(k || ''));

/* ═════════════════════════════════════════════ 상태 */
const S = {
  meta: null, cats: [], virt: {}, settings: {},
  drives: [], dmap: {}, dmapLower: {}, scans: {}, jobs: [], busy: false, skew: 0,
  update: null, previews: null, projCount: null,
  recs: null, recsVer: 0, recsLoading: false,
  route: { name: 'home' }, stack: [],
  sel: new Map(), anchor: null,
  screen: null,
  my: new Map(), jobSeen: new Map(), scanSeen: {},
  online: 'on', firstState: true, stateVer: 0,
  seen: new Set(), introNow: true, recPrev: {}, pendKeys: new Set(), copyKeys: new Set(),
};
const DEFAULT_CATS = [
  ['cache', '캐시·임시'], ['render', '렌더·출력'], ['source', '촬영·소스'], ['proxy', '프록시'], ['video', '기타 영상'],
  ['project', '프로젝트'], ['image', '이미지·시퀀스'], ['audio', '오디오'], ['archive', '압축·백업'], ['app', '앱·시스템'], ['other', '기타'],
].map(([key, name], id) => ({ id, key, name, deletable: id !== 9, desc: '' }));
const catOf = i => S.cats[i] || DEFAULT_CATS[i] || DEFAULT_CATS[10];
const catName = i => (i == null ? '기타' : catOf(i).name);
/* un = 스캔한 드라이브에서 확인하지 못한 공간(시스템·권한 없음 등) · ns = 아직 스캔하지 않은 드라이브의 사용 공간 */
const SEGK = ['c0', 'c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c7', 'c8', 'c9', 'c10', 'bin', 'un', 'ns'];
function segName(k) {
  if (k === 'bin') return (S.virt.bin && S.virt.bin.name) || '휴지통';
  if (k === 'un') return (S.virt.unscanned && S.virt.unscanned.name) || '시스템·확인 불가';
  if (k === 'ns') return '아직 스캔 안 함';
  if (k === 'free') return '남은 공간';
  if (k === 'in') return '옮겨 올 항목';
  return catName(Number(k.slice(1)));
}
/* 화면에 쓰는 장치 꼬리표: 문자 드라이브는 「C:」, NAS 는 없음, 연결 안 된 장치는 마지막 문자 */
function dtag(d) {
  if (!d) return '';
  if (isLetterKey(d.letter)) return d.letter + ':';
  if (d.last_letter && isLetterKey(d.last_letter)) return d.last_letter + ':';
  return '';
}
const defaultLabel = d => (d && d.kind === 'network' ? 'NAS' : d && (d.kind === 'usb' || d.external) ? '외장 디스크' : '로컬 디스크');
const dlabel = d => (d ? d.label || defaultLabel(d) : '');
const dname = d => (d ? (dtag(d) ? `${dlabel(d)} (${dtag(d)})` : dlabel(d)) : '');
/* 조사: 받침이 있으면 「으로」 — 드라이브 문자는 읽는 소리 기준 */
function ro(word) {
  const s = String(word || '').replace(/[\s)\]」』:.·]+$/, '');
  const c = s.slice(-1);
  if (!c) return '로';
  if (/[A-Za-z]/.test(c)) return /[MNmn]/.test(c) ? '으로' : '로';   // 엠·엔만 받침(ㄹ 받침은 「로」)
  if (/[0-9]/.test(c)) return '036'.includes(c) ? '으로' : '로';      // 영·삼·육
  const code = c.charCodeAt(0) - 0xAC00;
  if (code < 0 || code > 11171) return '로';
  const jong = code % 28;
  return jong === 0 || jong === 8 ? '로' : '으로';
}
const dnameRo = d => { const n = dname(d); return n + ro(n); };
/* 을/를 · 은/는 · 이/가 — 마지막 글자의 받침으로 고른다 */
function josa(word, withJong, without) {
  const s = String(word || '').replace(/[\s)\]」』:.·]+$/, '');
  const c = s.slice(-1);
  let jong = false;
  if (/[A-Za-z]/.test(c)) jong = /[LMNRlmnr]/.test(c);
  else if (/[0-9]/.test(c)) jong = '013678'.includes(c);
  else { const code = c.charCodeAt(0) - 0xAC00; jong = code >= 0 && code <= 11171 && code % 28 !== 0; }
  return word + (jong ? withJong : without);
}
function driveOf(L) {
  const k = String(L || '');
  if (!k) return null;
  return S.dmap[k] || S.dmap[k.toUpperCase()] || S.dmapLower[k.toLowerCase()] || null;
}
const liveDrives = () => S.drives.filter(isConn);
/* 경로 → 장치(가장 긴 루트). Windows·UNC 는 대소문자를 가리지 않는다 */
function rootHit(p) {
  if (!p) return null;
  const pl = String(p).replace(/\//g, '\\').toLowerCase();
  let best = null, bl = -1;
  for (const d of (S.drives || [])) {
    const r = String(d.root || '').replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase();
    if (!r) continue;
    if ((pl === r || pl.startsWith(r + '\\')) && r.length > bl) { best = d; bl = r.length; }
  }
  if (!best && p[1] === ':') { const d = driveOf(p[0].toUpperCase()); if (d) best = d; }
  return best;
}
const letterOfPath = p => { const d = rootHit(p); return d ? d.letter : (p && p[1] === ':' ? p[0].toUpperCase() : ''); };
const drivePath = p => rootHit(p);
const isScanning = L => { const s = S.scans[L]; return !!(s && (s.phase === 'scan' || s.phase === 'finalize')); };
const TERM = st => st === 'done' || st === 'partial' || st === 'failed' || st === 'canceled';

/* ═════════════════════════════════════════════ API */
class ApiError extends Error {
  constructor(status, data) {
    super((data && data.message) || '요청을 처리하지 못했어요.');
    this.status = status; this.data = data || {}; this.code = this.data.error || 'error';
  }
}
async function apiCall(method, path, body, query) {
  let url = path;
  if (query) {
    const qs = new URLSearchParams();
    for (const k in query) if (query[k] !== undefined && query[k] !== null) qs.set(k, query[k]);
    url += '?' + qs.toString();
  }
  const opt = { method, headers: { 'X-DB-Token': TOKEN, Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(url, opt); } catch (e) { throw new ApiError(0, { error: 'network', message: '앱 서버에 연결할 수 없어요.' }); }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok || !data || data.ok === false) throw new ApiError(res.status, data || { error: 'bad_response', message: '응답을 읽지 못했어요.' });
  return data;
}
const GET = (p, q) => apiCall('GET', p, undefined, q);
const POST = (p, b) => apiCall('POST', p, b || {});

/* ═════════════════════════════════════════════ 툴팁(data-tip) */
const Tip = {
  el: null, cur: null, timer: 0, hidAt: 0, rich: false,
  init() {
    this.el = $('#tip');
    document.addEventListener('pointerover', e => {
      if (this.rich) return;
      const t = e.target && e.target.closest ? e.target.closest('[data-tip]') : null;
      if (t === this.cur) return;
      clearTimeout(this.timer);
      if (!t) { this.hide(); return; }
      this.cur = t;
      const quick = this.el.classList.contains('show') || performance.now() - this.hidAt < 450;
      this.timer = setTimeout(() => this.show(t), quick ? 30 : 420);
    });
    document.addEventListener('pointerout', e => { if (!e.relatedTarget) this.hide(); });
    document.addEventListener('pointerdown', () => this.hide(), true);
    window.addEventListener('blur', () => this.hide());
    document.addEventListener('scroll', () => this.hide(), true);
    // 떠 있는 툴팁의 대상이 data-tip 을 바꾸면(스캔 버튼이 「취소」↔「모두 스캔」으로 바뀌는 등) 그 자리에서 새 문구로 바꾼다.
    // 포인터가 같은 요소 안에 머무르면 pointerover 가 다시 오지 않아 옛 문구가 남는다.
    if (window.MutationObserver) {
      new MutationObserver(ms => { if (this.cur && ms.some(m => m.target === this.cur)) this.refresh(); })
        .observe(document.documentElement, { attributes: true, attributeFilter: ['data-tip'], subtree: true });
    }
  },
  refresh() {
    const t = this.cur;
    if (!t || this.rich || !this.el.classList.contains('show')) return;   // 아직 안 떴으면 show() 가 그때의 문구를 읽는다
    const txt = t.isConnected ? t.getAttribute('data-tip') : '';
    if (!txt) { this.hide(); return; }
    if (this.el.textContent !== txt) this.show(t);
  },
  show(t) {
    if (!t.isConnected || document.body.classList.contains('dragging')) return;
    const txt = t.getAttribute('data-tip');
    if (!txt) return;
    this.el.textContent = txt;
    const r = t.getBoundingClientRect();
    this.place(r.left + r.width / 2, r.top, r.bottom);
  },
  showHTML(html, x, top, bottom) {
    this.rich = true;
    clearTimeout(this.timer);
    this.el.innerHTML = html;
    this.place(x, top, bottom);
  },
  place(cx, top, bottom) {
    const el = this.el;
    el.style.left = '0px'; el.style.top = '0px';
    const w = el.offsetWidth, hh = el.offsetHeight;
    let x = clamp(cx - w / 2, 8, innerWidth - w - 8);
    let y = top - hh - 8;
    if (y < 8) y = bottom + 8;
    el.style.left = Math.round(x) + 'px';
    el.style.top = Math.round(y) + 'px';
    el.classList.add('show');
  },
  hide() {
    clearTimeout(this.timer);
    this.cur = null;
    this.rich = false;
    if (this.el && this.el.classList.contains('show')) { this.el.classList.remove('show'); this.hidAt = performance.now(); }
  },
};

/* 연동 하이라이트: 범례·세그먼트 호버 → 모든 막대에서 그 카테고리만 밝게 */
const HL = {
  key: null, listeners: new Set(),
  set(k) {
    if (k === this.key) return;
    this.key = k;
    if (k) document.body.dataset.hl = k; else delete document.body.dataset.hl;
    $$('.lg.hl').forEach(e => e.classList.remove('hl'));
    if (k) $$(`.lg[data-c="${k}"]`).forEach(e => e.classList.add('hl'));
    this.listeners.forEach(fn => { try { fn(k); } catch (e) { /* 무시 */ } });
  },
};

/* ═════════════════════════════════════════════ 오버레이 스크롤바 */
function attachScrollbar(sc, host) {
  host = host || sc.parentElement;
  if (!host || sc._sb) return;
  if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
  const sb = h('div', { class: 'sb' }, h('i'));
  const th = sb.firstChild;
  host.append(sb);
  sc._sb = sb;
  let hideT = 0, drag = null;
  const upd = () => {
    const H = sc.clientHeight, SH = sc.scrollHeight;
    if (SH <= H + 1) { sb.style.display = 'none'; return; }
    sb.style.display = '';
    const r = sc.getBoundingClientRect(), hr = host.getBoundingClientRect();
    sb.style.top = (r.top - hr.top + 4) + 'px';
    sb.style.height = (H - 8) + 'px';
    sb.style.bottom = 'auto';
    const track = H - 8;
    const tH = Math.max(28, track * H / SH);
    const y = (track - tH) * (sc.scrollTop / (SH - H));
    th.style.height = tH + 'px';
    th.style.transform = `translateY(${y}px)`;
  };
  const flash = (ms = 900) => { upd(); sb.classList.add('on'); clearTimeout(hideT); hideT = setTimeout(() => { if (!drag && !sb.classList.contains('hover')) sb.classList.remove('on'); }, ms); };
  sc.addEventListener('scroll', () => flash(), { passive: true });
  host.addEventListener('pointermove', e => {
    if (sb.style.display === 'none') return;
    const r = sb.getBoundingClientRect();
    const near = e.clientX >= r.left - 6 && e.clientX <= r.right + 2 && e.clientY >= r.top && e.clientY <= r.bottom;
    sb.classList.toggle('hover', near);
    if (near) flash();
  });
  host.addEventListener('pointerleave', () => sb.classList.remove('hover'));
  th.addEventListener('pointerdown', e => {
    e.preventDefault(); e.stopPropagation();
    drag = { y: e.clientY, st: sc.scrollTop };
    sb.classList.add('drag');
    th.setPointerCapture(e.pointerId);
  });
  th.addEventListener('pointermove', e => {
    if (!drag) return;
    const H = sc.clientHeight, SH = sc.scrollHeight, track = H - 8;
    const tH = Math.max(28, track * H / SH);
    sc.scrollTop = drag.st + (e.clientY - drag.y) * (SH - H) / Math.max(1, track - tH);
  });
  const end = () => { drag = null; sb.classList.remove('drag'); flash(); };
  th.addEventListener('pointerup', end);
  th.addEventListener('pointercancel', end);
  sb.addEventListener('pointerdown', e => {
    if (e.target !== sb) return;
    const r = sb.getBoundingClientRect();
    sc.scrollTop += (e.clientY < r.top + parseFloat(th.style.transform.slice(11)) ? -1 : 1) * sc.clientHeight * 0.9;
  });
  let ro = null, mo = null;
  if (window.ResizeObserver) {
    ro = new ResizeObserver(upd);
    ro.observe(sc);
    if (sc.firstElementChild) ro.observe(sc.firstElementChild);
    mo = new MutationObserver(() => { if (sc.firstElementChild) ro.observe(sc.firstElementChild); upd(); });
    mo.observe(sc, { childList: true });
  }
  upd();
  return { update: upd, flash: ms => { if (sc.scrollHeight > sc.clientHeight + 1) flash(ms); }, destroy() { if (ro) ro.disconnect(); if (mo) mo.disconnect(); sb.remove(); sc._sb = null; } };
}

/* ═════════════════════════════════════════════ 누적 막대 */
class Bar {
  constructor(size = '', opts = {}) {
    this.keys = opts.keys || SEGK;
    this.el = h('div', { class: 'bar ' + size });
    this.fill = h('div', { class: 'fill' });
    this.el.append(this.fill);
    this.segs = {};
    for (const k of this.keys) {
      const s = h('i', { class: 'seg', 'data-c': k });
      this.fill.append(s);
      this.segs[k] = s;
    }
    this.vals = {};
    this.total = 0;
    this.first = true;
    this.revealOnFirst = opts.reveal !== false && !reduced && S.introNow !== false;
    if (this.revealOnFirst) this.el.classList.add('reveal');
    if (opts.tip !== false) this.bindTip();
  }
  set(vals, total) {
    const first = this.first;
    if (first) this.el.classList.add('noanim');
    let acc = 0;
    const t = total > 0 ? total : 1;
    for (const k of this.keys) {
      const b = Math.max(0, vals[k] || 0);
      let w = Math.min(100 - acc, b / t * 100);
      if (k === 'in' && b > 0) w = Math.min(100 - acc, Math.max(w, 0.7));
      const s = this.segs[k];
      const L = acc.toFixed(3) + '%', W = Math.max(0, w).toFixed(3) + '%';
      if (s._l !== L) { s.style.left = L; s._l = L; }
      if (s._w !== W) { s.style.width = W; s._w = W; }
      acc += Math.max(0, w);
    }
    this.vals = vals;
    this.total = total;
    if (first) {
      this.first = false;
      void this.el.offsetWidth;
      this.el.classList.remove('noanim');
      if (this.revealOnFirst) raf2(() => this.el.classList.add('go'));
    }
  }
  setScanning(on) { this.el.classList.toggle('scanning', !!on); }
  hit(x) {
    const r = this.el.getBoundingClientRect();
    const f = (x - r.left) / Math.max(1, r.width);
    const t = this.total || 1;
    let acc = 0;
    for (const k of this.keys) {
      const w = (this.vals[k] || 0) / t;
      if (w > 0 && f >= acc && f < acc + w) return k;
      acc += w;
    }
    return f >= acc && f <= 1 ? 'free' : null;
  }
  bindTip() {
    this.el.addEventListener('pointermove', e => {
      if (document.body.classList.contains('dragging')) return;
      const k = this.hit(e.clientX);
      if (!k) { Tip.hide(); HL.set(null); return; }
      const t = this.total || 1;
      let b;
      if (k === 'free') { b = t - sum(this.keys.map(x => this.vals[x] || 0)); } else b = this.vals[k] || 0;
      const r = this.el.getBoundingClientRect();
      const sw = k === 'free' ? 'var(--track-hi)' : k === 'un' ? 'var(--hatch)' : k === 'ns' ? 'var(--hatch-ns)' : k === 'in' ? 'var(--lime)' : k === 'bin' ? 'var(--cbin)' : `var(--${k})`;
      Tip.showHTML(`<div class="tt"><i style="background:${sw}"></i>${esc(segName(k))}</div><div class="ts"><b>${fmtBytes(b)}</b> · 전체의 ${fmtPct(b, t)}</div>`, e.clientX, r.top, r.bottom);
      HL.set(k === 'in' ? null : k);
    });
    this.el.addEventListener('pointerleave', () => { Tip.hide(); HL.set(null); });
  }
}

function driveVals(d) {
  const v = {};
  if (!d) return v;
  const sc = S.scans[d.letter];
  const live = sc && (sc.phase === 'scan' || sc.phase === 'finalize') && !d.scanned;
  const cats = live ? (sc.cats || []) : (d.cats || []);
  for (let i = 0; i < NCAT; i++) v['c' + i] = cats[i] || 0;
  v.bin = d.bin_bytes || 0;
  if (live) v.un = Math.max(0, (d.used || 0) - (sc.bytes || 0) - v.bin);
  else v.un = d.unscanned_bytes != null ? d.unscanned_bytes : Math.max(0, (d.used || 0) - (d.scanned_bytes || 0) - v.bin);
  // 스캔한 적 없는 드라이브의 나머지는 「확인하지 못한」 공간이 아니라 「아직 안 본」 공간이다(시스템 파일로 읽히면 안 된다).
  // 스캔 중인 드라이브는 진행에 따라 줄어드는 「확인 불가」로 둔다.
  if (!d.scanned && !live) { v.ns = v.un; v.un = 0; }
  return v;
}
function addVals(a, b) { const o = Object.assign({}, a); for (const k in b) o[k] = (o[k] || 0) + (b[k] || 0); return o; }
function totals() {
  let total = 0, used = 0, free = 0, vals = {}, bin = 0, binN = 0, scanned = 0, lastScan = 0;
  for (const d of liveDrives()) {
    total += d.total || 0; used += d.used || 0; free += d.free || 0;
    vals = addVals(vals, driveVals(d));
    bin += d.bin_bytes || 0; binN += (d.bin_bytes || 0) > 0 ? 1 : 0;
    if (d.scanned) scanned++;
    if (d.scanned_at && d.scanned_at > lastScan) lastScan = d.scanned_at;
  }
  return { total, used, free, vals, bin, binN, scanned, lastScan };
}

/* 범례 칩 */
class Legend {
  constructor(opts = {}) {
    this.el = h('div', { class: 'legend' });
    this.chips = {};
    this.onPick = opts.onPick || null;
    for (const k of [...SEGK, 'free']) {
      const nm = h('span', { text: segName(k) });
      const v = h('b', { class: 'num', text: '' });
      const st = k === 'free' || k === 'un' || k === 'ns';
      const c = h(st ? 'span' : 'button', { class: 'lg' + (st ? ' static' : ''), 'data-c': k, type: 'button' }, h('i'), nm, v);
      if (k === 'un') c.setAttribute('data-tip', '스캔한 드라이브에서 사용 중이지만 확인하지 못한 공간이에요.\n시스템 파일·권한이 없는 폴더·볼륨 섀도 복사본 등이에요.');
      if (k === 'ns') c.setAttribute('data-tip', '아직 스캔하지 않은 드라이브가 쓰는 공간이에요.\n스캔하면 무엇이 차지하는지 보여 드려요.');
      c.addEventListener('pointerenter', () => HL.set(k === 'free' ? 'free' : k));
      c.addEventListener('pointerleave', () => HL.set(null));
      if (c.tagName === 'BUTTON') c.addEventListener('click', () => this.onPick && this.onPick(k));
      c.hidden = true;
      this.el.append(c);
      this.chips[k] = { el: c, v };
    }
  }
  set(vals, free) {
    for (const k of SEGK) {
      const b = vals[k] || 0;
      const ch = this.chips[k];
      ch.el.hidden = !(b > 0);
      if (b > 0) tw(ch.v, b, fmtBytes);
    }
    const f = this.chips.free;
    f.el.hidden = !(free > 0);
    if (free > 0) tw(f.v, free, fmtBytes);
  }
}

/* 카테고리 타일 */
function tile(c, glyph, size = '') {
  return h('span', { class: 'tile ' + size, 'data-c': c, html: ic(glyph || c) });
}
function itemTile(it, size = 't-sm') {
  const c = 'c' + (it.cat == null ? 10 : it.cat);
  if (it.kind === 'dir') return h('span', { class: 'tile ic ' + size, 'data-c': c, html: ic('folder') });
  if (it.kind === 'loose') return h('span', { class: 'tile ic ' + size, 'data-c': 'c10', html: ic('c10') });
  if (it.kind === 'seq') return h('span', { class: 'tile ic ' + size, 'data-c': c, html: ic('seq') });
  return h('span', { class: 'tile ic ' + size, 'data-c': c, html: ic(c) });
}

/* 공통 컴포넌트: 탭(미끄러지는 표시) */
function Tabs(items, cur, onChange, cls = '') {
  const el = h('div', { class: 'tabs ' + cls, role: 'tablist' });
  const ind = h('span', { class: 'ind' });
  el.append(ind);
  let curId = cur;
  const btns = items.map(it => {
    const b = h('button', { type: 'button', role: 'tab', class: it.id === cur ? 'on' : '', text: it.label });
    b.addEventListener('click', () => { if (curId !== it.id) { set(it.id); onChange(it.id); } });
    el.append(b);
    return b;
  });
  let placed = false;
  function place(anim) {
    const i = items.findIndex(x => x.id === curId);
    const b = btns[i];
    if (!b || !b.offsetWidth) return;
    if (!anim || reduced) ind.style.transition = 'none';
    ind.style.width = b.offsetWidth + 'px';
    ind.style.transform = `translateX(${b.offsetLeft}px)`;
    if (!anim || reduced) { void ind.offsetWidth; ind.style.transition = ''; }
    placed = true;
  }
  function set(id) { curId = id; btns.forEach((b, i) => b.classList.toggle('on', items[i].id === id)); place(placed); }
  if (window.ResizeObserver) new ResizeObserver(() => place(false)).observe(el);
  requestAnimationFrame(() => place(false));
  return { el, set, get value() { return curId; } };
}

/* ═════════════════════════════════════════════ 팝오버 · 메뉴 */
const Pop = {
  stack: [],
  open(anchor, content, o = {}) {
    const el = h('div', { class: 'pop ' + (o.cls || '') }, content);
    el.addEventListener('pointerdown', e => e.stopPropagation());
    document.body.append(el);
    const p = { el, anchor, o, closed: false, close: () => Pop.close(p), place: () => Pop.place(p) };
    this.place(p);
    raf2(() => el.classList.add('show'));
    this.stack.push(p);
    if (o.onOpen) o.onOpen(p);
    return p;
  },
  place(p) {
    const { el, anchor, o } = p;
    const w = el.offsetWidth, hh = el.offsetHeight, gap = o.gap == null ? 8 : o.gap;
    let x, y, ox = '50%', oy = '0%';
    const place = o.place || 'bottom-end';
    if (anchor && anchor.getBoundingClientRect) {
      const r = anchor.getBoundingClientRect();
      if (place.startsWith('top')) { y = r.top - hh - gap; oy = '100%'; } else { y = r.bottom + gap; oy = '0%'; }
      if (place.endsWith('end')) { x = r.right - w; ox = '100%'; } else if (place.endsWith('start')) { x = r.left; ox = '0%'; } else { x = r.left + r.width / 2 - w / 2; }
      if (y + hh > innerHeight - 8 && !place.startsWith('top')) { y = r.top - hh - gap; oy = '100%'; }
      if (y < 8) { y = r.bottom + gap; oy = '0%'; }
    } else {
      const pt = anchor || { x: innerWidth / 2, y: innerHeight / 2 };
      x = pt.x; y = pt.y; ox = '0%'; oy = '0%';
      if (pt.side === 'right') { x = pt.x; y = pt.y; }
      if (x + w > innerWidth - 8) { x = pt.side === 'right' && pt.x0 != null ? pt.x0 - w : pt.x - w; ox = '100%'; }
      if (y + hh > innerHeight - 8) { y = Math.max(8, innerHeight - 8 - hh); oy = '100%'; }
    }
    x = clamp(x, 8, innerWidth - w - 8);
    y = clamp(y, 8, Math.max(8, innerHeight - hh - 8));
    el.style.left = Math.round(x) + 'px';
    el.style.top = Math.round(y) + 'px';
    el.style.setProperty('--ox', ox);
    el.style.setProperty('--oy', oy);
  },
  close(p) {
    if (!p || p.closed) return;
    p.closed = true;
    const i = this.stack.indexOf(p);
    if (i >= 0) {
      const above = this.stack.slice(i + 1);
      this.stack.splice(i);
      above.forEach(q => { q.closed = true; q.el.remove(); if (q.o.onClose) q.o.onClose(); });
    }
    p.el.classList.remove('show');
    p.el.classList.add('closing');
    setTimeout(() => p.el.remove(), 160);
    if (p.o.onClose) p.o.onClose();
  },
  closeAll() { [...this.stack].reverse().forEach(p => this.close(p)); },
  top() { return this.stack[this.stack.length - 1] || null; },
};
document.addEventListener('pointerdown', e => {
  if (!Pop.stack.length) return;
  const t = Pop.top();
  if (t && t.anchor && t.anchor.contains && t.anchor.contains(e.target)) return;
  Pop.closeAll();
});
window.addEventListener('resize', () => Pop.closeAll());

/* 메뉴: items = [{label, icon, onClick, danger, disabled, tip, sub:[…], right, dot, check} | '-' | {head}] */
const Menu = {
  open(at, items, o = {}) {
    if (!o.keep) Pop.closeAll();
    const box = h('div', { class: 'menu', role: 'menu' });
    let subPop = null;
    const btns = [];
    for (const it of items) {
      if (it === '-') { box.append(h('div', { class: 'msep' })); continue; }
      if (it.head) { box.append(h('div', { class: 'mhead', text: it.head })); continue; }
      const b = h('button', { class: 'mi' + (it.danger ? ' danger' : ''), type: 'button', role: 'menuitem', disabled: it.disabled ? true : null, 'data-tip': it.tip || null });
      if (it.check !== undefined) b.append(h('span', { class: 'chk', html: it.check ? ic('check') : '' }));
      if (it.icon) b.append(icEl(it.icon));
      if (it.dot != null) { const dd = h('span', { class: 'dot', style: { '--sc': it.dot } }); b.append(dd); }
      b.append(h('span', { class: 'lbl', text: it.label }));
      if (it.right || it.sub) b.append(h('span', { class: 'r', html: (it.right ? esc(it.right) : '') + (it.sub ? ic('chevR') : '') }));
      if (it.sub) {
        const openSub = () => {
          if (subPop && !subPop.closed && subPop._for === b) return;
          if (subPop) Pop.close(subPop);
          const r = b.getBoundingClientRect();
          subPop = Menu.open({ x: r.right + 4, y: r.top - 5, side: 'right', x0: r.left - 4 }, it.sub, { keep: true });
          subPop._for = b;
          btns.forEach(x => x.classList.toggle('act', x === b));
        };
        b.addEventListener('pointerenter', () => { clearTimeout(b._t); b._t = setTimeout(openSub, 120); });
        b.addEventListener('pointerleave', () => clearTimeout(b._t));
        b.addEventListener('click', openSub);
      } else {
        b.addEventListener('pointerenter', () => {
          if (subPop && !subPop.closed) { Pop.close(subPop); subPop = null; btns.forEach(x => x.classList.remove('act')); }
        });
        b.addEventListener('click', () => { Pop.closeAll(); if (it.onClick) it.onClick(); });
      }
      btns.push(b);
      box.append(b);
    }
    const p = Pop.open(at, box, { cls: 'menu-pop', place: o.place, gap: o.gap, onClose: o.onClose });
    p.btns = btns;
    return p;
  },
};

/* ═════════════════════════════════════════════ 시트(확인창) */
const Sheet = {
  cur: null,
  open(o) {
    if (this.cur) this.close(true);
    Pop.closeAll();
    const layer = $('#layer');
    const scrim = h('div', { class: 'scrim' });
    const x = h('button', { class: 'ibtn sm x', type: 'button', 'aria-label': '닫기', html: ic('x') });
    const head = h('div', { class: 'sh-h' }, o.icon || null, h('div', { style: { minWidth: '0', flex: '1' } }, h('h3', { text: o.title || '' }), o.sub ? h('p', { text: o.sub }) : null), x);
    const inner = h('div', { class: 'scroll' });
    if (o.body) inner.append(o.body);
    const bodyWrap = h('div', { class: 'sh-b' }, inner);
    const foot = h('div', { class: 'sh-f' });
    const el = h('div', { class: 'sheet ' + (o.cls || ''), role: 'dialog', 'aria-modal': 'true' }, head, bodyWrap, foot);
    layer.append(scrim, el);
    const sh = {
      el, body: inner, foot, head, closed: false, primary: null, busy: false,
      close: () => Sheet.close(),
      shake() { el.classList.remove('shake'); void el.offsetWidth; el.classList.add('shake'); },
      setTitle(t, s) { $('h3', head).textContent = t; let p = $('p', head); if (s != null) { if (!p) { p = h('p'); $('h3', head).after(p); } p.textContent = s; } },
      setFoot(nodes) { foot.textContent = ''; nodes.forEach(n => n && foot.append(n)); },
    };
    sh._scrim = scrim;
    sh._onClose = o.onClose;
    x.addEventListener('click', () => this.close());
    scrim.addEventListener('pointerdown', () => { if (!sh.busy) this.close(); });
    sh._sbar = attachScrollbar(inner, bodyWrap);
    this.cur = sh;
    raf2(() => { scrim.classList.add('show'); el.classList.add('show'); });
    setTimeout(() => { const f = sh.primary && !sh.primary.disabled ? sh.primary : x; try { f.focus({ preventScroll: true }); } catch (e) { /* */ } }, 60);
    return sh;
  },
  close(instant) {
    const sh = this.cur;
    if (!sh || sh.closed) return;
    sh.closed = true;
    this.cur = null;
    sh._scrim.classList.remove('show');
    sh.el.classList.remove('show');
    sh.el.classList.add('closing');
    const rm = () => { if (sh._sbar) sh._sbar.destroy(); sh._scrim.remove(); sh.el.remove(); };
    if (instant) rm(); else setTimeout(rm, 220);
    if (sh._onClose) sh._onClose();
  },
};

/* ═════════════════════════════════════════════ 토스트 */
const Toast = {
  show(o) {
    const box = $('#toasts');
    while (box.children.length >= 3) box.firstChild.remove();
    const kind = o.kind || 'ok';
    const icn = { ok: 'check', err: 'warn', info: 'info', lime: 'check' }[kind] || 'info';
    const acts = h('div', { class: 'ta' });
    const el = h('div', { class: 'toast', role: 'status' },
      h('span', { class: 'ti ' + kind, html: ic(icn) }),
      h('div', { class: 'tb' }, h('div', { class: 'tt', text: o.title || '' }), o.sub ? h('div', { class: 'ts', text: o.sub }) : null),
      acts);
    const life = o.timeout || (o.actions && o.actions.length ? 8000 : 4200);
    const line = h('i', { class: 'tl' });
    el.append(line);
    let t0 = performance.now(), left = life, timer = 0, anim = null;
    const close = () => { clearTimeout(timer); el.classList.add('out'); setTimeout(() => el.remove(), 230); };
    const arm = () => {
      clearTimeout(timer);
      t0 = performance.now();
      timer = setTimeout(close, left);
      if (!reduced && line.animate) {
        anim = line.animate([{ transform: `scaleX(${left / life})` }, { transform: 'scaleX(0)' }], { duration: left, easing: 'linear', fill: 'forwards' });
      }
    };
    for (const a of (o.actions || [])) {
      const b = h('button', { class: 'btn sm ' + (a.primary ? 'lime' : 'ghost'), type: 'button', text: a.label });
      b.addEventListener('click', () => { close(); a.onClick && a.onClick(); });
      acts.append(b);
    }
    acts.append(h('button', { class: 'ibtn xs', type: 'button', 'aria-label': '닫기', html: ic('x'), onclick: close }));
    el.addEventListener('pointerenter', () => { clearTimeout(timer); left -= performance.now() - t0; if (anim) anim.pause(); });
    el.addEventListener('pointerleave', () => { if (left < 600) left = 600; if (anim) anim.cancel(); arm(); });
    box.append(el);
    arm();
    return { close };
  },
  err(e, title) {
    const msg = e && e.message ? e.message : String(e || '');
    return this.show({ kind: 'err', title: title || msg, sub: title ? msg : null });
  },
};

/* ═════════════════════════════════════════════ 선택 · 행 등록부 */
const REG = { byKey: new Map(), byPath: new Map() };
function regClear() { REG.byKey.clear(); REG.byPath.clear(); }
function regRow(it, el) { REG.byKey.set(it.key, { it, el }); if (it.path && it.kind !== 'loose') REG.byPath.set(pk(it.path), it.key); }
function unregRow(key) { const r = REG.byKey.get(key); if (r) { REG.byKey.delete(key); if (r.it.path && REG.byPath.get(pk(r.it.path)) === key) REG.byPath.delete(pk(r.it.path)); } }
const selectable = it => it && it.kind !== 'loose' && !it.protected && !it._pending && !!it.path;

const Sel = {
  toggle(it, force) {
    if (!selectable(it)) return;
    const on = force == null ? !S.sel.has(it.key) : !!force;
    if (on) S.sel.set(it.key, it); else S.sel.delete(it.key);
    this.syncRow(it.key);
    S.anchor = it.key;
    Tray.update();
  },
  set(items, on = true) {
    for (const it of items) {
      if (!selectable(it)) continue;
      if (on) S.sel.set(it.key, it); else S.sel.delete(it.key);
      this.syncRow(it.key);
    }
    Tray.update();
  },
  clear() {
    if (!S.sel.size) return;
    const keys = [...S.sel.keys()];
    S.sel.clear();
    keys.forEach(k => this.syncRow(k));
    S.anchor = null;
    Tray.update();
  },
  syncRow(key) {
    const r = REG.byKey.get(key);
    if (r && r.el) r.el.classList.toggle('sel', S.sel.has(key));
    if (S.screen && S.screen.onSel) S.screen.onSel();
  },
  items() { return [...S.sel.values()]; },
  /* 프로젝트 화면처럼 자기 선택 상태를 가진 화면이 통째로 넘긴다(그 화면에는 다시 알리지 않는다) */
  replace(keys, items) {
    const prev = [...S.sel.keys()];
    S.sel.clear();
    (items || []).forEach((it, i) => {
      if (!it) return;
      const k = (keys && keys[i]) || it.key || it.path;
      const x = Object.assign({}, it, { key: k });
      if (selectable(x)) S.sel.set(k, x);
    });
    prev.forEach(k => this.syncRow(k));
    this.quiet = true;
    try { Tray.update(); } finally { this.quiet = false; }
  },
  listeners: new Set(), quiet: false, sig: '',
  notify() {
    const keys = [...S.sel.keys()];
    const sig = keys.join('\n');
    if (sig === this.sig) return;
    this.sig = sig;
    if (this.quiet) return;
    this.listeners.forEach(fn => { try { fn(keys.slice()); } catch (e) { console.warn('select listener', e); } });
  },
};
function compOf(items) {
  const cb = new Array(NCAT).fill(0);
  for (const it of items) {
    if (it.cat_bytes && it.cat_bytes.length) it.cat_bytes.forEach((b, i) => { cb[i] += b || 0; });
    else cb[it.cat == null ? 10 : it.cat] += it.size || 0;
  }
  return cb;
}
const cbVals = cb => { const v = {}; cb.forEach((b, i) => { v['c' + i] = b; }); return v; };
function compLabel(cb, total) {
  const order = cb.map((b, i) => [b, i]).filter(x => x[0] > 0).sort((a, b) => b[0] - a[0]);
  if (!order.length) return '';
  return order.slice(0, 2).map(([b, i]) => `${catName(i)} ${fmtPct(b, total)}`).join(' · ') + (order.length > 2 ? ' 외' : '');
}

/* ═════════════════════════════════════════════ 선택 트레이 */
const Tray = {
  el: null, bar: null, shown: false,
  init() {
    this.el = $('#tray');
    this.el.inert = true;
    this.el.setAttribute('aria-hidden', 'true');
    $('#trCk').innerHTML = ic('check');
    this.bar = new Bar('b-sm', { reveal: false });
    $('#trComp').append(this.bar.el);
    const rb = $('#trRecycle'); rb.innerHTML = ic('trash') + '<span>휴지통으로</span>';
    const mb = $('#trMove'); mb.innerHTML = ic('move') + '<span>이동</span>' + ic('chevD', 'cv');
    const cb = $('#trCopy'); cb.innerHTML = ic('copy') + '<span>복사</span>' + ic('chevD', 'cv');
    mb.setAttribute('data-tip', '다른 드라이브·폴더로 옮겨요 (원본은 검증 후 지워요)');
    cb.setAttribute('data-tip', '다른 드라이브·폴더에 복사해요 (원본은 그대로)');
    rb.addEventListener('click', () => { const it = Sel.items(); if (it.length) confirmRecycle(it); });
    mb.addEventListener('click', () => { const it = Sel.items(); if (it.length) MovePop.toggle(mb, it, 'move'); });
    cb.addEventListener('click', () => { const it = Sel.items(); if (it.length) MovePop.toggle(cb, it, 'copy'); });
    $('#trClear').addEventListener('click', () => Sel.clear());
    $('#trCk').addEventListener('click', () => Sel.clear());
    $('#trCk').setAttribute('data-tip', '선택 해제 (Esc)');
  },
  recycleMode(items) {
    const nb = items.filter(i => !hasBin(driveOf(i.drive) || drivePath(i.path)));
    return nb.length === items.length && items.length ? 'delete' : nb.length ? 'mixed' : 'bin';
  },
  update() {
    const items = Sel.items();
    Sel.notify();
    const n = items.length;
    const show = n > 0;
    if (show !== this.shown) {
      this.shown = show;
      this.el.classList.toggle('show', show);
      // 숨은 트레이의 버튼이 Tab 순서에 남지 않게(보이지 않는 포커스 4칸)
      this.el.inert = !show;
      if (show) this.el.removeAttribute('aria-hidden'); else this.el.setAttribute('aria-hidden', 'true');
      document.body.classList.toggle('tray-on', show);
      if (!show) { const t = Pop.top(); if (t && t.o.cls && t.o.cls.includes('mvpop')) Pop.closeAll(); }
    }
    if (!show) return;
    const total = sum(items.map(i => i.size));
    const nEl = $('#trN');
    const txt = fmtNum(n) + '개';
    if (nEl.textContent !== txt) {
      nEl.textContent = txt;
      nEl.classList.remove('bump'); void nEl.offsetWidth; nEl.classList.add('bump');
    }
    tw($('#trS'), total, fmtBytes, 420);
    const cb = compOf(items);
    this.bar.set(cbVals(cb), sum(cb) || 1);
    $('#trCompL').textContent = compLabel(cb, sum(cb));
    const rm = this.recycleMode(items);
    const rb = $('#trRecycle');
    const want = rm === 'delete' ? 'del' : 'bin';
    if (rb._m !== want) {
      rb._m = want;
      rb.className = 'btn ' + (want === 'del' ? 'solid-danger' : 'danger');
      rb.innerHTML = ic('trash') + `<span>${want === 'del' ? '삭제(복구 불가)' : '휴지통으로'}</span>`;
      if (want === 'del') rb.setAttribute('data-tip', '휴지통이 없는 위치예요. 지우면 되돌릴 수 없어요.'); else rb.removeAttribute('data-tip');
    }
    const t = Pop.top();
    if (t && t.o.cls && t.o.cls.includes('mvpop')) MovePop.refresh(items);
  },
};

/* ═════════════════════════════════════════════ 이동·복사 팝오버(대상별 미리보기) */
const MODE = {
  move: { verb: '이동', ing: '이동 중', q: '어디로 옮길까요?', keep: '원래 폴더 구조를 그대로 유지해요', icon: 'move', after: '이동 후' },
  copy: { verb: '복사', ing: '복사 중', q: '어디에 복사할까요?', keep: '원본은 그대로 남아요', icon: 'copy', after: '복사 후' },
};
/* 폴더 고르기: 창 모드면 네이티브 폴더 선택 창, 브라우저면 null(호출자가 입력 칸을 보여 준다) */
async function pickFolderNative(initial) {
  if (!(S.meta && S.meta.dialog)) return { inline: true };
  try {
    const r = await POST('/api/pick_folder', { initial: initial || null });
    if (r.canceled || !r.path) return { canceled: true };
    return { path: r.path, known: r.known };
  } catch (e) {
    if (e.code === 'no_dialog') return { inline: true };
    throw e;
  }
}
function pathInputRow(onOk, initial, label) {
  const inp = h('input', { class: 'tin', type: 'text', spellcheck: 'false', placeholder: '예: D:\\보관\\2026  또는  \\\\ORICO\\영상백업\\보관' });
  if (initial) inp.value = initial;
  const ok = h('button', { class: 'btn sm primary', type: 'button', text: label || '이 폴더로' });
  const err = h('div', { class: 'why' });
  const go = () => {
    const v = inp.value.trim();
    if (!v) { inp.focus(); return; }
    if (!/^[A-Za-z]:[\\/]/.test(v) && !/^[\\/]{2}[^\\/]+[\\/][^\\/]/.test(v) && !v.startsWith('/')) { err.textContent = '전체 경로를 입력해 주세요. 예: D:\\보관'; inp.focus(); return; }
    onOk(v);
  };
  ok.addEventListener('click', go);
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); go(); } });
  const el = h('div', { class: 'mv-path' }, h('div', { class: 'mv-path-r' }, inp, ok), err);
  setTimeout(() => { try { inp.focus(); inp.select(); } catch (e) { /* */ } }, 30);
  return { el, inp, err };
}
const MovePop = {
  p: null, items: null, gen: 0, mode: 'move', anchor: null,
  toggle(anchor, items, mode = 'move') {
    if (this.p && !this.p.closed && this.mode === mode) { Pop.close(this.p); return; }
    this.open(anchor, items, { mode });
  },
  open(anchor, items, o = {}) {
    Pop.closeAll();
    this.items = items;
    this.mode = o.mode || 'move';
    this.anchor = anchor;
    const M = MODE[this.mode];
    const bytes = sum(items.map(i => i.size));
    const box = h('div', {},
      h('div', { class: 'hd' }, h('h4', {}, h('span', { class: 'mv-ic ' + this.mode, html: ic(M.icon) }), M.q),
        h('p', { class: 'num', text: `${fmtNum(items.length)}개 · ${fmtBytes(bytes)} · ${M.keep}` })));
    const list = h('div', { class: 'mv-list scroll' });
    const lw = h('div', { class: 'mv-lw' }, list);
    box.append(lw);
    const foot = h('div', { class: 'mv-foot' });
    box.append(foot);
    this.list = list;
    this.foot = foot;
    this.build(list, items);
    this.buildFoot();
    const cls = 'mvpop ' + this.mode;
    this.p = Pop.open(anchor, box, { cls, place: o.place || 'top', gap: 12, onClose: () => { this.p = null; } });
    this.sb = attachScrollbar(list, lw);
    const fade = () => lw.classList.toggle('fade', list.scrollHeight > list.clientHeight + 2 && list.scrollTop + list.clientHeight < list.scrollHeight - 6);
    list.addEventListener('scroll', fade, { passive: true });
    this.fade = fade;
    requestAnimationFrame(() => { this.fit(); if (this.sb) this.sb.flash(1400); });   // 넘치면 처음부터 스크롤 막대를 잠깐 보여 준다
  },
  refresh(items) { if (!this.p || this.p.closed) return; this.items = items; this.build(this.list, items); this.fit(); },
  /* 목록이 잘리는 자리가 묶음 제목(「외장·USB」) 바로 아래면 그 묶음이 비어 보인다 → 제목 위에서 자른다 */
  fit() {
    const list = this.list;
    if (!list || !this.p || this.p.closed) return;
    if (list.scrollTop === 0) {
      list.style.maxHeight = '';
      if (list.scrollHeight > list.clientHeight + 2) {
        const lr = list.getBoundingClientRect();
        for (const gh of list.querySelectorAll('.mv-grp')) {
          const r = gh.getBoundingClientRect();
          if (r.top >= lr.bottom) break;
          const nx = gh.nextElementSibling;
          const nr = nx ? nx.getBoundingClientRect() : r;
          if (nr.top + Math.min(nr.height, 36) > lr.bottom) {   // 제목은 보이는데 첫 줄이 거의 안 보인다
            list.style.maxHeight = Math.max(120, Math.floor(r.top - lr.top)) + 'px';
            break;
          }
        }
      }
    }
    Pop.place(this.p);
    if (this.fade) this.fade();
  },
  fitSoon() {
    if (this._fitR) return;
    this._fitR = requestAnimationFrame(() => { this._fitR = 0; this.fit(); });
  },
  buildFoot() {
    const foot = this.foot;
    foot.textContent = '';
    const row = h('button', { class: 'mvrow pick', type: 'button' }, h('span', { class: 'tile', 'data-c': 'drive', html: ic('folderPlus') }),
      h('div', { class: 'l1' }, h('span', { class: 'n', text: '폴더 선택…' }), h('span', { class: 'a', text: S.meta && S.meta.dialog ? '아무 폴더나 골라요' : '경로를 입력해요' })));
    row.addEventListener('click', () => this.pick(null));
    foot.append(row);
  },
  async pick(initial) {
    const items = this.items, mode = this.mode;
    let r;
    try { r = await pickFolderNative(initial); } catch (e) { Toast.err(e, '폴더 선택 창을 열지 못했어요'); return; }
    if (r.canceled) return;
    if (r.inline) {
      if (!this.p || this.p.closed) return;
      this.foot.textContent = '';
      const pr = pathInputRow(v => { Pop.closeAll(); confirmMove(items, null, { mode, destDir: v, from: this.anchor }); }, initial || '');
      this.foot.append(h('div', { class: 'mv-pick-h', text: mode === 'copy' ? '복사할 폴더 경로' : '옮길 폴더 경로' }), pr.el);
      Pop.place(this.p);
      return;
    }
    Pop.closeAll();
    confirmMove(items, null, { mode, destDir: r.path, from: this.anchor });
  },
  build(list, items) {
    const g = ++this.gen;
    const mode = this.mode, M = MODE[mode];
    list.textContent = '';
    const paths = items.map(i => i.path);
    const bytes = sum(items.map(i => i.size));
    const srcL = new Set(paths.map(letterOfPath));
    const live = liveDrives();
    for (const grp of GROUPS) {
      const ds = live.filter(d => driveGroup(d) === grp.id);
      if (!ds.length) continue;
      if (live.length > 2) list.append(h('div', { class: 'mv-grp', text: grp.label }));
      for (const d of ds) {
        const same = srcL.size === 1 && srcL.has(d.letter);
        const bar = new Bar('b-sm', { keys: [...SEGK, 'in'], reveal: false });
        const after = h('span', { class: 'a num' });
        const why = h('div', { class: 'why' });
        const row = h('button', { class: 'mvrow', type: 'button', 'data-l': d.letter },
          tile('drive', driveIcon(d)),
          h('div', { class: 'l1' }, h('span', { class: 'n', html: `${esc(dlabel(d))}${dtag(d) ? `<em>${esc(dtag(d))}</em>` : ''}` }),
            !hasBin(d) ? h('span', { class: 'kchip nb', 'data-tip': '휴지통이 없는 위치예요', text: '휴지통 없음' }) : null, after),
          h('div', {}, bar.el, why));
        list.append(row);
        const vals = driveVals(d);
        if (same) {
          bar.set(vals, d.total);
          after.innerHTML = `<b>${fmtBytes(d.free)}</b> 남음`;
          if (mode === 'copy') {
            row.classList.add('same');
            why.textContent = '같은 드라이브예요 · 눌러서 복사할 폴더를 골라요';
            why.style.color = 'var(--text3)';
            row.onclick = () => this.pick(d.root);
          } else {
            row.classList.add('off', 'same');
            why.textContent = '선택한 항목이 있는 드라이브예요';
          }
          continue;
        }
        const est = (d.free || 0) - bytes;
        bar.set(Object.assign({}, vals, { in: bytes }), d.total);
        after.innerHTML = `${M.after} <b>${fmtBytes(Math.max(0, est))}</b> 남음`;
        row.disabled = true;
        bar.el.classList.add('loading');
        POST('/api/ops/preview_move', { paths, target: d.letter, mode }).then(pv => {
          if (g !== this.gen) return;
          this.fitSoon();   // 안내 줄이 생기면 줄 높이가 바뀐다
          bar.el.classList.remove('loading');
          bar.set(Object.assign({}, vals, { in: pv.bytes }), d.total);
          after.innerHTML = `${M.after} <b>${fmtBytes(Math.max(0, pv.target_free_after))}</b> 남음`;
          const blocked = pv.blocked || [];
          if (blocked.length) {
            row.classList.add('off');
            const sameD = blocked.every(b => letterOfPath(b.path) === d.letter);
            why.textContent = sameD ? '선택한 항목 중 일부가 이미 이 드라이브에 있어요' : (blocked[0].reason || '옮길 수 없는 항목이 있어요');
            return;
          }
          if (!pv.enough) {
            row.classList.add('off');
            after.innerHTML = `<b>${fmtBytes(pv.target_free)}</b> 남음`;
            why.textContent = `공간이 부족해요 · ${fmtBytes(pv.bytes + MARGIN)} 필요(여유 1GB 포함)`;
            return;
          }
          row.disabled = false;
          why.style.color = 'var(--text3)';
          if (pv.conflicts) why.textContent = `같은 이름 ${fmtNum(pv.conflicts)}개는 「(2)」를 붙여 ${mode === 'copy' ? '복사해요' : '옮겨요'}`;
          else if (d.kind === 'network') why.textContent = '네트워크라 느릴 수 있어요 · 속도와 남은 시간을 보여 드려요';
          else why.textContent = '';
          row.onclick = () => { const its = this.items; Pop.closeAll(); confirmMove(its, d.letter, { mode, pv, from: this.anchor }); };
        }).catch(e => {
          if (g !== this.gen) return;
          this.fitSoon();
          bar.el.classList.remove('loading');
          row.classList.add('off');
          why.textContent = e.message;
        });
      }
    }
  },
};

/* ═════════════════════════════════════════════ 확인 시트 */
function compBlock(cb, total, label = '구성') {
  const bar = new Bar('b-sm', { reveal: false });
  bar.set(cbVals(cb), total || sum(cb) || 1);
  const list = h('div', { class: 'comp-list' });
  cb.map((b, i) => [b, i]).filter(x => x[0] > 0).sort((a, b) => b[0] - a[0]).slice(0, 6).forEach(([b, i]) => {
    list.append(h('div', { style: { '--sc': `var(--c${i})` } }, h('i'), h('span', { text: catName(i) }), h('b', { class: 'num', text: fmtBytes(b) })));
  });
  return h('div', { class: 'blk' }, h('div', { class: 'lab', text: label }), bar.el, list);
}
function stat(l, v) { return h('div', { class: 'stat' }, h('div', { class: 'l', text: l }), h('div', { class: 'v num', text: v })); }
function note(kind, iconName, ...kids) { return h('div', { class: 'note ' + kind }, icEl(iconName), h('div', {}, ...kids)); }
function skelBody() {
  return h('div', {},
    h('div', { class: 'stats3' }, [0, 1, 2].map(() => h('div', { class: 'stat' }, h('div', { class: 'skl', style: { width: '40%', height: '10px' } }), h('div', { class: 'skl', style: { width: '70%', height: '16px', marginTop: '8px' } })))),
    h('div', { class: 'skl', style: { height: '8px', margin: '4px 0 18px' } }),
    h('div', { class: 'skl', style: { height: '64px', borderRadius: '12px' } }));
}
const titleFor = (items, verb) => items.length === 1 ? `이 항목을 ${verb}` : `${fmtNum(items.length)}개 항목을 ${verb}`;
const nameOf = p => String(p || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop();
/* 시트 부제: 앞의 두 이름. 이름이 같으면(중복 파일) 장치 꼬리표 → 그래도 같으면 상위 폴더까지 붙여 구분한다 */
function subFor(items) {
  if (items.length === 1) return items[0].name;
  const lead = items.slice(0, 2);
  const same = a => a[0].toLowerCase() === a[1].toLowerCase();
  let names = lead.map(i => i.name);
  if (same(names)) {
    const tagOf = i => { const d = driveOf(i.drive) || drivePath(i.path); return dtag(d) || dlabel(d) || ''; };
    names = lead.map(i => `${tagOf(i)} ${i.name}`.trim());
    if (same(names)) {
      names = lead.map(i => {
        const s = String(i.path || '').replace(/[\\/]+$/, '');
        const sep = s.includes('\\') ? '\\' : '/';
        const parts = s.split(/[\\/]/);
        return `${tagOf(i)} …${sep}${parts.slice(-2).join(sep)}`.trim();
      });
    }
  }
  return `${names.join(', ')}${items.length > 2 ? ` 외 ${fmtNum(items.length - 2)}개` : ''}`;
}
/* 중복 파일 묶음 중 남은 사본을 전부 지우게 되는 묶음(남길 사본이 없다) */
function wipedDupeGroups(items) {
  const keys = new Set(items.map(i => i.key));
  const seen = new Set(), out = [];
  for (const it of items) {
    const g = it && it._dg;
    if (!g || seen.has(g)) continue;
    seen.add(g);
    const left = [...g.keys].filter(k => { const r = REG.byKey.get(k); return !(r && r.it && r.it._pending); });
    if (left.length && left.every(k => keys.has(k))) out.push(g);
  }
  return out;
}
function wipedNote(wiped) {
  return note('danger', 'warn',
    h('b', { text: wiped.length === 1 ? `「${wiped[0].name}」의 사본을 모두 골랐어요. ` : `모든 사본을 지우는 묶음이 ${fmtNum(wiped.length)}개 있어요. ` }),
    '남길 사본이 없어요. 하나는 남기려면 그 사본의 선택을 해제하세요.');
}
/* 경로 줄이기 — 드라이브/루트는 남기고 가운데 폴더를 「…」로, 끝 폴더들은 남긴다.
   Windows(C:\), UNC(\\서버\공유\), POSIX(가짜 드라이브 루트) 모두 같은 규칙. */
function splitPath(p) {
  p = String(p || '');
  const sep = p.includes('\\') || p[1] === ':' ? '\\' : '/';
  const d = rootHit(p);
  let root = '';
  if (d && d.root) {
    const r = String(d.root).replace(/[\\/]+$/, '');
    root = p.slice(0, r.length);
  } else if (/^[A-Za-z]:/.test(p)) root = p.slice(0, 2);
  else if (/^[\\/]{2}[^\\/]+[\\/][^\\/]+/.test(p)) root = p.match(/^[\\/]{2}[^\\/]+[\\/][^\\/]+/)[0];
  const rest = p.slice(root.length);
  const segs = rest.split(/[\\/]+/).filter(Boolean);
  let rootDisp = root;
  if (root && !/^[A-Za-z]:$/.test(root) && !/^[\\/]{2}/.test(root) && root.length > 22) {
    const rs = root.split(/[\\/]+/).filter(Boolean);
    rootDisp = '…' + sep + rs.slice(-2).join(sep);   // 가짜 드라이브의 긴 임시 폴더 루트
  }
  return { p, root, rootDisp, sep, segs };
}
function midStr(s, max) {
  s = String(s || '');
  if (s.length <= max) return s;
  const a = Math.ceil((max - 1) * 0.6), b = Math.max(1, max - 1 - a);
  return s.slice(0, a) + '…' + s.slice(-b);
}
function fmtPath(p, max = 64) {
  const { rootDisp, sep, segs } = splitPath(p);
  const head = rootDisp ? rootDisp + sep : (String(p || '').startsWith(sep) ? sep : '');
  const full = head + segs.join(sep);
  if (full.length <= max || segs.length === 0) return full.length <= max ? full : midStr(full, max);
  let tail = segs[segs.length - 1];
  if (head.length + 2 + tail.length > max) return head + '…' + sep + midStr(tail, Math.max(8, max - head.length - 2));
  let i = segs.length - 2;
  while (i >= 1 && head.length + 2 + segs[i].length + 1 + tail.length <= max) { tail = segs[i] + sep + tail; i--; }
  if (i < 0) return full;
  if (i === 0 && head.length + segs[0].length + 1 + tail.length <= max) return head + segs[0] + sep + tail;
  // 첫 폴더(드라이브 바로 아래)는 맥락이라 들어가면 남긴다
  if (i >= 1 && head.length + segs[0].length + 3 + tail.length <= max) return head + segs[0] + sep + '…' + sep + tail;
  return head + '…' + sep + tail;
}
const midPath = fmtPath;
/* 행 안의 경로: 머리(루트)는 고정, 꼬리는 좁아지면 앞에서부터 「…」 — 글자 수 예산 + CSS 이중 안전 */
function pathEl(p, max = 84) {
  const s = fmtPath(p, max);
  const { rootDisp, sep } = splitPath(p);
  let head = rootDisp ? rootDisp + sep : '';
  if (head && !s.startsWith(head)) head = '';
  let tail = s.slice(head.length);
  const m = tail.match(/^((?:[^\\/]+[\\/])?…[\\/])/);
  if (m) { head += m[1]; tail = tail.slice(m[1].length); }
  const el = h('span', { class: 'pth' }, head ? h('span', { class: 'ph', text: head }) : null, h('span', { class: 'pt' }, h('bdi', { text: tail })));
  if (s !== String(p || '')) el.setAttribute('data-tip', String(p || ''));
  return el;
}

function blockedNote(blocked) {
  return note('danger', 'lock', h('b', { text: `보호된 항목 ${fmtNum(blocked.length)}개가 있어 진행할 수 없어요.` }),
    h('ul', {}, blocked.slice(0, 4).map(b => h('li', { text: `${nameOf(b.path)} — ${b.reason || b.protected_reason || '보호된 항목이에요.'}` }))),
    blocked.length > 4 ? h('div', { text: `외 ${fmtNum(blocked.length - 4)}개` }) : null);
}

async function confirmRecycle(items, o = {}) {
  items = items.filter(selectable);
  if (!items.length) return;
  const rm = Tray.recycleMode(items);
  if (rm === 'delete') return confirmDelete(items, o);
  let skipped = [];
  if (rm === 'mixed') {
    skipped = items.filter(i => !hasBin(driveOf(i.drive) || drivePath(i.path)));
    items = items.filter(i => !skipped.includes(i));
  }
  const paths = items.map(i => i.path);
  const wiped = wipedDupeGroups(items);
  const go = h('button', { class: 'btn danger', type: 'button', html: ic('trash') + '<span>휴지통으로 보내기</span>', disabled: true });
  const cancel = h('button', { class: 'btn ghost', type: 'button', text: '취소' });
  const sh = Sheet.open({
    icon: tile('bin', 'trash', 't-lg'),
    title: titleFor(items, '휴지통으로 보낼까요?'),
    sub: subFor(items),
    body: skelBody(),
  });
  sh.primary = go;
  sh.setFoot([h('span', { class: 'hint', text: wiped.length ? '확인을 체크해야 보낼 수 있어요 · Esc 로 닫기' : 'Enter 로 실행 · Esc 로 닫기' }), h('span', { class: 'sp' }), cancel, go]);
  cancel.addEventListener('click', () => sh.close());
  let ins;
  try { ins = await POST('/api/inspect', { paths }); } catch (e) { if (!sh.closed) { sh.body.textContent = ''; sh.body.append(note('danger', 'warn', e.message)); } return; }
  if (sh.closed) return;
  const body = h('div', {});
  const exist = ins.items.filter(x => x.exists !== false);
  const missing = ins.items.filter(x => x.exists === false);
  const blocked = ins.items.filter(x => x.protected);
  body.append(h('div', { class: 'stats3' }, stat('크기', fmtBytes(ins.total)), stat('파일', fmtNum(ins.total_files) + '개'), stat('항목', fmtNum(exist.length) + '개')));
  // 중복 파일: 한 묶음의 사본을 전부 고르면 남는 사본이 없다 — 경고하고 따로 확인받는다(Enter 한 번으로 못 지나가게)
  let wipeOk = !wiped.length;
  if (wiped.length) body.append(wipedNote(wiped));
  body.append(compBlock(ins.cat_bytes, ins.total));
  // 폴더 안의 다른 카테고리도 함께 지워진다는 안내
  const lvc = S.screen && S.screen.lv ? S.screen.lv.o.cat : S.route.cat;
  const routeCat = lvc != null ? lvc : null;
  const dirs = ins.items.filter(x => x.kind === 'dir');
  if (dirs.length) {
    const cb = new Array(NCAT).fill(0);
    dirs.forEach(x => (x.cat_bytes || []).forEach((b, i) => { cb[i] += b || 0; }));
    const others = cb.map((b, i) => [b, i]).filter(([b, i]) => b > 0 && i !== routeCat).sort((a, b) => b[0] - a[0]);
    if (routeCat != null && others.length) {
      body.append(note('warn', 'folder', h('b', { text: '폴더 안의 다른 항목도 함께 보내요. ' }),
        others.slice(0, 3).map(([b, i]) => `${catName(i)} ${fmtBytes(b)}`).join(' · ') + (others.length > 3 ? ' 외' : '')));
    }
  }
  const letters = [...new Set(paths.map(letterOfPath))].filter(Boolean);
  const binInfo = letters.map(L => { const d = driveOf(L); return d ? `${dtag(d) || dlabel(d)} 휴지통 지금 ${fmtBytes(d.bin_bytes)}` : ''; }).filter(Boolean).join(' · ');
  body.append(note('warn', 'info', h('b', { text: '휴지통으로 보낸 용량은 휴지통을 비워야 그 드라이브에서 확보됩니다.' }), h('div', { class: 'num', style: { marginTop: '3px' }, text: binInfo })));
  if (skipped.length) {
    const ls = [...new Set(skipped.map(i => dname(driveOf(i.drive) || drivePath(i.path))))].join(', ');
    body.append(note('', 'info', h('b', { text: `${fmtNum(skipped.length)}개는 함께 보내지 않아요. ` }), `${ls}에는 휴지통이 없어요. 그 항목은 따로 골라 「삭제(복구 불가)」로 지울 수 있어요.`));
  }
  if (missing.length) body.append(note('', 'info', `${fmtNum(missing.length)}개는 이미 없어서 건너뛰어요.`));
  if (blocked.length) body.append(blockedNote(blocked.map(b => ({ path: b.path, reason: b.protected_reason }))));
  const sync = () => { go.disabled = !!blocked.length || !exist.length || !wipeOk; };
  if (wiped.length) body.append(checkRow('남길 사본 없이 모든 사본을 휴지통으로 보낸다는 것을 확인했어요', v => { wipeOk = v; sync(); }));
  sh.body.textContent = '';
  sh.body.append(body);
  sync();
  if (!go.disabled && !wiped.length) go.focus({ preventScroll: true });
  go.addEventListener('click', async () => {
    if (go.disabled) return;
    go.disabled = true; sh.busy = true;
    go.innerHTML = ic('spinner', 'spin') + '<span>보내는 중</span>';
    const okPaths = exist.map(x => x.path);
    try {
      const r = await POST('/api/ops/recycle', { paths: okPaths });
      const keys = okPaths.map(p => REG.byPath.get(pk(p))).filter(Boolean);
      Jobs.track(r.job_id, { kind: 'recycle', keys, paths: okPaths, title: titleFor(items, '휴지통으로') });
      markPending(keys, true);
      Sel.clear();
      sh.busy = false;
      sh.close();
      if (o.onDone) o.onDone();
    } catch (e) {
      sh.busy = false;
      sync();
      go.innerHTML = ic('trash') + '<span>휴지통으로 보내기</span>';
      sh.shake();
      if (e.code === 'no_bin') { sh.close(); confirmDelete(items, o); return; }
      sh.body.append(e.code === 'protected' ? blockedNote(e.data.blocked || []) : note('danger', 'warn', e.message));
    }
  });
}

/* 영구 삭제 직전 서버가 「확인창보다 늘었다」(409 changed) 고 거절했을 때 — 무엇이 늘었는지와 다시 스캔 */
function grownNote(data, sh) {
  const g = (data && data.grown) || [];
  const lines = g.slice(0, 3).map(x => `${nameOf(x.path)}: ${fmtBytes(x.confirmed_bytes)} → ${fmtBytes(x.bytes)} · 파일 ${fmtNum(x.confirmed_files)} → ${fmtNum(x.files)}개`);
  const letters = [...new Set(g.map(x => letterOfPath(x.path)).filter(Boolean))];
  const kids = [h('b', { text: '확인창을 띄운 뒤 내용이 늘어서 지우지 않았어요. ' }),
    '보여 드린 것보다 더 지우지 않도록 멈췄어요. 다시 스캔한 뒤 확인해 주세요.',
    ...lines.map(t => h('div', { class: 'num', style: { marginTop: '3px' }, text: t })),
    g.length > 3 ? h('div', { style: { marginTop: '3px' }, text: `외 ${fmtNum(g.length - 3)}개` }) : null];
  if (letters.length) {
    kids.push(h('div', { style: { marginTop: '8px' } }, h('button', { class: 'btn sm', type: 'button', html: ic('scan') + '<span>다시 스캔</span>',
      onclick: () => { if (sh) sh.close(); startScan(letters); } })));
  }
  return note('danger', 'warn', ...kids.filter(Boolean));
}

/* 영구 삭제 — 휴지통이 없는 위치(NAS·USB 메모리)용. 체크박스 확인 1회(SPEC-2 A.1) */
function checkRow(label, onChange) {
  const ck = h('span', { class: 'ck', html: ic('check') });
  const el = h('button', { class: 'ckrow', type: 'button', role: 'checkbox', 'aria-checked': 'false' }, ck, h('span', { text: label }));
  let on = false;
  el.addEventListener('click', () => { on = !on; el.classList.toggle('on', on); ck.classList.toggle('on', on); el.setAttribute('aria-checked', on ? 'true' : 'false'); onChange(on); });
  return el;
}
async function confirmDelete(items, o = {}) {
  items = items.filter(selectable);
  if (!items.length) return;
  const paths = items.map(i => i.path);
  const go = h('button', { class: 'btn solid-danger', type: 'button', html: ic('trash') + '<span>영구 삭제</span>', disabled: true });
  const cancel = h('button', { class: 'btn ghost', type: 'button', text: '취소' });
  const sh = Sheet.open({
    icon: h('span', { class: 'tile t-lg danger-tile', html: ic('trash') }),
    title: titleFor(items, '영구 삭제할까요?'),
    sub: subFor(items),
    body: skelBody(), cls: 'danger',
  });
  sh.primary = go;
  sh.setFoot([h('span', { class: 'hint', text: '되돌릴 수 없어요' }), h('span', { class: 'sp' }), cancel, go]);
  cancel.addEventListener('click', () => sh.close());
  let ins;
  try { ins = await POST('/api/inspect', { paths }); } catch (e) { if (!sh.closed) { sh.body.textContent = ''; sh.body.append(note('danger', 'warn', e.message)); } return; }
  if (sh.closed) return;
  const exist = ins.items.filter(x => x.exists !== false);
  const blocked = ins.items.filter(x => x.protected);
  const body = h('div', {});
  body.append(h('div', { class: 'stats3' }, stat('크기', fmtBytes(ins.total)), stat('파일', fmtNum(ins.total_files) + '개'), stat('항목', fmtNum(exist.length) + '개')));
  body.append(compBlock(ins.cat_bytes, ins.total, '지울 항목 구성'));
  const where = [...new Set(items.map(i => dname(driveOf(i.drive) || drivePath(i.path))).filter(Boolean))].join(', ');
  const nb = items.every(i => !hasBin(driveOf(i.drive) || drivePath(i.path)));
  body.append(note('danger', 'warn', h('b', { text: '복구할 수 없어요. ' }),
    nb ? `${where || '이 위치'}에는 휴지통이 없어서 휴지통을 거치지 않고 바로 지워져요.` : '휴지통을 거치지 않고 바로 지워져요.'));
  if (blocked.length) body.append(blockedNote(blocked.map(b => ({ path: b.path, reason: b.protected_reason }))));
  const wiped = wipedDupeGroups(items);
  if (wiped.length) body.append(wipedNote(wiped));
  let agreed = false;
  const sync = () => { go.disabled = !agreed || !!blocked.length || !exist.length; };
  const ackText = wiped.length
    ? `남길 사본 없이 ${fmtNum(exist.length)}개 항목(${fmtBytes(ins.total)})을 지우고, 되돌릴 수 없다는 것을 확인했어요`
    : `${fmtNum(exist.length)}개 항목(${fmtBytes(ins.total)})을 되돌릴 수 없다는 것을 확인했어요`;
  body.append(checkRow(ackText, v => { agreed = v; sync(); }));
  sh.body.textContent = '';
  sh.body.append(body);
  sync();
  go.addEventListener('click', async () => {
    if (go.disabled) return;
    go.disabled = true; sh.busy = true;
    go.innerHTML = ic('spinner', 'spin') + '<span>지우는 중</span>';
    const okPaths = exist.map(x => x.path);
    // 확인창이 보여 준 양 — 그 뒤 디스크에서 늘었으면 서버가 409 changed 로 거절한다(동의한 것보다 더 지우지 않게)
    const expect = exist.map(x => ({ path: x.path, bytes: x.size || 0, files: x.files || 0 }));
    try {
      const r = await POST('/api/ops/delete', { paths: okPaths, confirm: true, expect });
      const keys = okPaths.map(p => REG.byPath.get(pk(p))).filter(Boolean);
      Jobs.track(r.job_id, { kind: 'delete', keys, paths: okPaths });
      markPending(keys, true);
      Sel.clear();
      sh.busy = false;
      sh.close();
      if (o.onDone) o.onDone();
    } catch (e) {
      sh.busy = false; sync();
      go.innerHTML = ic('trash') + '<span>영구 삭제</span>';
      sh.shake();
      if (e.code === 'changed') { sh.body.append(grownNote(e.data, sh)); go.disabled = true; return; }
      sh.body.append(e.code === 'protected' ? blockedNote(e.data.blocked || []) : note('danger', 'warn', e.message));
    }
  });
}

const VERIFY = [
  { id: 'size', label: '크기만', desc: '크기만 비교해요. 가장 빠르지만 손상은 못 잡아요.' },
  { id: 'quick', label: '빠른 확인', desc: '크기 + 처음·가운데·끝 1MB 를 비교해요. 권장.' },
  { id: 'full', label: '전체 비교', desc: '복사한 내용을 다시 읽어 해시를 비교해요. 가장 느리고 가장 확실해요.' },
];

/* 이동·복사 확인. target = 드라이브 키, 또는 null + o.destDir(고른 폴더) */
async function confirmMove(items, target, o = {}) {
  items = items.filter(selectable);
  const mode = o.mode === 'copy' ? 'copy' : 'move';
  const M = MODE[mode];
  const destDir = o.destDir || null;
  let td = target ? driveOf(target) : (destDir ? drivePath(destDir) : null);
  if (!items.length || (!td && !destDir)) return;
  const paths = items.map(i => i.path);
  const goLabel = mode === 'copy' ? '복사하기' : '옮기기';
  const go = h('button', { class: 'btn primary', type: 'button', html: ic(M.icon) + `<span>${goLabel}</span>`, disabled: true });
  const cancel = h('button', { class: 'btn ghost', type: 'button', text: '취소' });
  const where = destDir ? '고른 폴더' : dname(td);
  const title = mode === 'copy' ? titleFor(items, `${where}에 복사할까요?`) : titleFor(items, `${where}${ro(where)} 옮길까요?`);
  const sh = Sheet.open({
    icon: tile('drive', destDir ? 'folder' : driveIcon(td), 't-lg'),
    title,
    sub: mode === 'copy' ? '복사하고 검증해요. 원본은 그대로 남아요.' : '복사하고 검증한 뒤에 원본을 지워요. 중간에 멈춰도 원본은 남아요.',
    body: skelBody(),
    cls: 'wide',
  });
  sh.primary = go;
  sh.setFoot([h('span', { class: 'hint', text: 'Enter 로 실행 · Esc 로 닫기' }), h('span', { class: 'sp' }), cancel, go]);
  cancel.addEventListener('click', () => sh.close());
  let ins, pv;
  try {
    const want = { paths, target: target || null, dest_dir: destDir, mode };
    [ins, pv] = await Promise.all([POST('/api/inspect', { paths }), o.pv && o.pv.dest_examples && o.pv.mode === mode ? Promise.resolve(o.pv) : POST('/api/ops/preview_move', want)]);
  } catch (e) { if (!sh.closed) { sh.body.textContent = ''; sh.body.append(note('danger', 'warn', e.message)); } return; }
  if (sh.closed) return;
  const T = pv.target || target;
  td = driveOf(T) || td;
  if (destDir && td) sh.setTitle(mode === 'copy' ? titleFor(items, `${dname(td)}의 고른 폴더에 복사할까요?`) : titleFor(items, `${dname(td)}의 고른 폴더로 옮길까요?`));
  const body = h('div', {});
  const tTag = td ? (dtag(td) || dlabel(td)) : '대상';
  body.append(h('div', { class: 'stats3' }, stat('크기', fmtBytes(pv.bytes)), stat('파일', fmtNum(pv.files) + '개'), stat(`${tTag} ${M.after} 남음`, fmtBytes(Math.max(0, pv.target_free_after)))));
  if (destDir) body.append(h('div', { class: 'blk' }, h('div', { class: 'lab', text: mode === 'copy' ? '복사할 폴더' : '옮길 폴더' }), h('div', { class: 'paths' }, h('div', { class: 'pr2' }, h('div', { class: 'd' }, icEl('folder'), pathEl(destDir, 80))))));
  if (td) {
    const vals = driveVals(td);
    const bar = new Bar('b-sm', { keys: [...SEGK, 'in'], reveal: false });
    bar.set(Object.assign({}, vals, { in: pv.bytes }), td.total);
    body.append(h('div', { class: 'blk' }, h('div', { class: 'lab' }, h('span', { text: `${dname(td)} ${M.after}` }), h('span', { class: 'sp' }), h('span', { class: 'num', text: `${fmtBytes(td.free)} → ${fmtBytes(Math.max(0, pv.target_free_after))}` })), bar.el));
  }
  // 원본 쪽: 이동은 확보될 몫, 복사는 그대로(SPEC-2 F)
  const srcLs = [...new Set(paths.map(letterOfPath))].filter(Boolean);
  if (srcLs.length === 1 && srcLs[0] !== T) {
    const sd = driveOf(srcLs[0]);
    if (sd) {
      const sv = driveVals(sd);
      const sbar = new Bar('b-sm', { keys: [...SEGK, 'in'], reveal: false });
      let right;
      if (mode === 'move') {
        const cb = ins.cat_bytes || [];
        const after = Object.assign({}, sv);
        cb.forEach((b, i) => { after['c' + i] = Math.max(0, (after['c' + i] || 0) - (b || 0)); });
        sbar.set(after, sd.total);
        right = `${fmtBytes(sd.free)} → ${fmtBytes(sd.free + (pv.bytes || 0))}`;
      } else {
        sbar.set(sv, sd.total);
        right = `${fmtBytes(sd.free)} 남음 · 변화 없음`;
      }
      body.append(h('div', { class: 'blk' }, h('div', { class: 'lab' }, h('span', { text: `원본 ${dname(sd)} ${mode === 'move' ? '이동 후' : '· 그대로예요'}` }), h('span', { class: 'sp' }), h('span', { class: 'num', text: right })), sbar.el));
    }
  }
  body.append(compBlock(ins.cat_bytes, ins.total, mode === 'copy' ? '복사할 항목 구성' : '옮길 항목 구성'));
  if (pv.dest_examples && pv.dest_examples.length) {
    const box = h('div', { class: 'paths' });
    pv.dest_examples.forEach(d => {
      const base = nameOf(d).replace(/ \(\d+\)(\.[^.]+)?$/, '$1');
      const src = paths.find(p => nameOf(p) === base) || '';
      box.append(h('div', { class: 'pr2' },
        src ? h('div', { class: 's' }, pathEl(src, 88)) : null,
        h('div', { class: 'd' }, icEl('arrowR'), pathEl(d, 84))));
    });
    if (paths.length > pv.dest_examples.length) box.append(h('div', { class: 'one more', text: `외 ${fmtNum(paths.length - pv.dest_examples.length)}개도 같은 방식으로 ${mode === 'copy' ? '복사해요' : '옮겨요'}` }));
    body.append(h('div', { class: 'blk' }, h('div', { class: 'lab', text: mode === 'copy' ? '복사할 위치' : '옮길 위치' }), box));
  }
  let verify = S.settings.verify || 'quick';
  const vdesc = h('div', { class: 'vdesc' });
  const vt = Tabs(VERIFY.map(v => ({ id: v.id, label: v.label })), verify, id => { verify = id; vdesc.textContent = VERIFY.find(v => v.id === id).desc; }, 'seg3');
  vdesc.textContent = VERIFY.find(v => v.id === verify).desc;
  body.append(h('div', { class: 'blk' }, h('div', { class: 'lab', text: '검증 수준' }), vt.el, vdesc));
  const blocked = (pv.blocked || []).concat(ins.items.filter(x => x.protected).map(x => ({ path: x.path, reason: x.protected_reason })));
  const seen = new Set();
  const blockedU = blocked.filter(b => { const k = pk(b.path); if (seen.has(k)) return false; seen.add(k); return true; });
  if (td && td.kind === 'network') body.append(note('', 'info', h('b', { text: '네트워크 위치예요. ' }), '속도는 공유기·NAS 에 따라 느릴 수 있어요. 진행 중에는 속도와 남은 시간을 작업 패널에서 보여 드려요.'));
  if (td && !hasBin(td) && mode === 'copy') body.append(note('', 'info', `${dname(td)}에는 휴지통이 없어요. 복사를 되돌리면 복사본을 영구 삭제해요(원본은 그대로).`));
  if (pv.conflicts) body.append(note('', 'info', `같은 이름이 이미 있는 항목 ${fmtNum(pv.conflicts)}개는 이름 뒤에 「(2)」를 붙여 ${mode === 'copy' ? '복사해요' : '옮겨요'}.`));
  const cacheB = (ins.cat_bytes || [])[0] || 0;
  if (cacheB > 0 && mode === 'move') body.append(note('', 'info', '캐시를 옮기면 After Effects·Premiere 가 다시 만들 수 있어요. 앱을 끈 뒤에 옮기는 게 안전해요.'));
  if (pv.need_dest) body.append(note('warn', 'folder', h('b', { text: '같은 드라이브 안에 복사하려면 폴더를 골라야 해요. ' }), '[복사 ▾] → 「폴더 선택…」으로 복사할 곳을 골라 주세요.'));
  if (blockedU.length && !pv.need_dest) body.append(blockedNote(blockedU));
  if (!pv.enough) body.append(note('danger', 'warn', h('b', { text: '공간이 부족해요. ' }), `${fmtBytes(pv.bytes + MARGIN)} 필요(여유 1GB 포함), ${fmtBytes(pv.target_free)} 남아 있어요.`));
  sh.body.textContent = '';
  sh.body.append(body);
  go.disabled = !!blockedU.length || !pv.enough || !!pv.need_dest;
  if (!go.disabled) go.focus({ preventScroll: true });
  go.addEventListener('click', async () => {
    if (go.disabled) return;
    go.disabled = true; sh.busy = true;
    go.innerHTML = ic('spinner', 'spin') + '<span>시작하는 중</span>';
    try {
      const r = await POST('/api/ops/move', { paths, target: target || null, dest_dir: destDir, verify, mode });
      const keys = paths.map(p => REG.byPath.get(pk(p))).filter(Boolean);
      Jobs.track(r.job_id, { kind: mode, keys, paths, target: T });
      if (mode === 'move') markPending(keys, true); else markCopying(keys, true);
      const fromRect = go.getBoundingClientRect();
      const label = `${fmtNum(items.length)}개 · ${fmtBytes(pv.bytes)}`;
      sh.busy = false;
      sh.close();
      Sel.clear();
      if (fromRect) flyTo(fromRect, railItem(T), label, mode === 'copy' ? 'copy' : 'lime');
      else pulseRail(T);
    } catch (e) {
      sh.busy = false;
      go.disabled = false;
      go.innerHTML = ic(M.icon) + `<span>${goLabel}</span>`;
      sh.shake();
      let msg = e.message;
      if (e.code === 'space') msg = `공간이 부족해요. ${fmtBytes(e.data.need)} 필요, ${fmtBytes(e.data.free)} 남아 있어요.`;
      sh.body.append(e.code === 'protected' ? blockedNote(e.data.blocked || []) : note('danger', 'warn', msg));
    }
  });
}

function confirmEmptyBin(letters) {
  const ds = liveDrives().filter(d => (d.bin_bytes || 0) > 0);
  if (!ds.length) { Toast.show({ kind: 'info', title: '휴지통이 비어 있어요.' }); return; }
  const pick = new Set((letters && letters.length ? letters : ds.map(d => d.letter)).filter(L => ds.some(d => d.letter === L)));
  if (!pick.size) ds.forEach(d => pick.add(d.letter));
  const go = h('button', { class: 'btn solid-danger', type: 'button' });
  const cancel = h('button', { class: 'btn ghost', type: 'button', text: '취소' });
  const totalEl = h('span', { class: 'hint num' });
  const list = h('div', { class: 'bincheck' });
  const upd = () => {
    const t = sum(ds.filter(d => pick.has(d.letter)).map(d => d.bin_bytes));
    go.innerHTML = ic('trash') + `<span>${pick.size ? fmtBytes(t) + ' 영구 삭제' : '드라이브를 고르세요'}</span>`;
    go.disabled = !pick.size;
    totalEl.textContent = `${fmtNum(pick.size)}개 드라이브 선택`;
    $$('.bcrow', list).forEach(r => r.querySelector('.ck').classList.toggle('on', pick.has(r.dataset.l)));
  };
  for (const d of ds) {
    const r = h('button', { class: 'bcrow', type: 'button', 'data-l': d.letter },
      h('span', { class: 'ck', html: ic('check') }), tile('drive', driveIcon(d), 't-sm'),
      h('div', {}, h('div', { class: 'n', text: dname(d) }), h('div', { class: 'c num', text: d.bin_count ? `항목 ${fmtNum(d.bin_count)}개` : '' })),
      h('span', { class: 'v num', text: fmtBytes(d.bin_bytes) }));
    r.addEventListener('click', () => { if (pick.has(d.letter)) pick.delete(d.letter); else pick.add(d.letter); upd(); });
    list.append(r);
  }
  const body = h('div', {}, list, h('div', { style: { height: '12px' } }),
    note('danger', 'warn', h('b', { text: '휴지통의 파일이 영구 삭제돼요. ' }), '되돌릴 수 없어요. 비운 만큼 그 드라이브의 남은 공간이 늘어나요.'));
  const sh = Sheet.open({ icon: tile('bin', 'trash', 't-lg'), title: '휴지통을 비울까요?', sub: '드라이브마다 휴지통이 따로 있어요.', body });
  sh.primary = go;
  sh.setFoot([totalEl, h('span', { class: 'sp' }), cancel, go]);
  cancel.addEventListener('click', () => sh.close());
  upd();
  go.addEventListener('click', async () => {
    if (!pick.size || go.disabled) return;
    go.disabled = true; sh.busy = true;
    try {
      const r = await POST('/api/ops/empty_bin', { drives: [...pick], confirm: true });
      Jobs.track(r.job_id, { kind: 'empty_bin', drives: [...pick] });
      sh.busy = false;
      sh.close();
    } catch (e) { sh.busy = false; go.disabled = false; sh.shake(); sh.body.append(note('danger', 'warn', e.message)); }
  });
}

/* 진행 중 표시 */
function markPending(keys, on) {
  for (const k of keys) {
    if (on) S.pendKeys.add(k); else S.pendKeys.delete(k);
    const r = REG.byKey.get(k);
    if (!r) continue;
    r.it._pending = on;
    if (r.el) r.el.classList.toggle('pending', on);
  }
}
/* 복사 중: 원본 행은 그대로 쓸 수 있고, 아래 줄무늬만 흐른다 */
function markCopying(keys, on) {
  for (const k of keys) {
    if (on) S.copyKeys.add(k); else S.copyKeys.delete(k);
    const r = REG.byKey.get(k);
    if (r && r.el) r.el.classList.toggle('copying', on);
  }
}
function pulseRows(keys, cls = 'copied') {
  for (const k of keys) {
    const r = REG.byKey.get(k);
    if (!r || !r.el) continue;
    r.el.classList.remove(cls); void r.el.offsetWidth; r.el.classList.add(cls);
    setTimeout(() => r.el && r.el.classList.remove(cls), 1300);
  }
}

/* ═════════════════════════════════════════════ 날아가는 칩(FLIP) */
const railItem = L => $(`.rl[data-l="${String(L || '').replace(/"/g, '')}"]`);
function pulseRail(L) {
  const el = railItem(L);
  if (!el) return;
  el.classList.remove('arrive'); void el.offsetWidth; el.classList.add('arrive');
  setTimeout(() => el.classList.remove('arrive'), 800);
}
function flyTo(from, toEl, label, kind = 'lime') {
  if (!toEl || reduced || !document.body.animate) { if (toEl) pulseRail(toEl.dataset.l); return; }
  const to = toEl.getBoundingClientRect();
  const outer = h('div', { class: 'flychip-y' });
  const icn = kind === 'danger' ? 'trash' : kind === 'copy' ? 'copy' : 'move';
  const chip = h('div', { class: 'flychip' + (kind === 'copy' ? ' copy' : ''), html: ic(icn) + `<span>${esc(label)}</span>` });
  if (kind === 'danger') { chip.style.background = 'var(--danger)'; chip.style.color = 'var(--on-color)'; }
  outer.append(chip);
  document.body.append(outer);
  const cw = chip.offsetWidth, ch = chip.offsetHeight;
  const x0 = from.left + from.width / 2 - cw / 2, y0 = from.top + from.height / 2 - ch / 2;
  const x1 = to.left + 24 - cw / 2, y1 = to.top + to.height / 2 - ch / 2;
  const dur = 640;
  outer.animate([{ transform: `translateY(${y0}px)` }, { transform: `translateY(${y1}px)` }], { duration: dur, easing: 'cubic-bezier(.2,.75,.25,1)', fill: 'forwards' });
  const a = chip.animate([
    { transform: `translateX(${x0}px) scale(1)`, opacity: 1 },
    { transform: `translateX(${x0 + (x1 - x0) * 0.35}px) scale(.92)`, opacity: 1, offset: 0.4 },
    { transform: `translateX(${x1}px) scale(.32)`, opacity: 0 },
  ], { duration: dur, easing: 'cubic-bezier(.55,0,.35,1)', fill: 'forwards' });
  a.onfinish = () => { outer.remove(); pulseRail(toEl.dataset.l); };
}

/* ═════════════════════════════════════════════ 드래그 앤 드롭(포인터 기반) — 기본 이동, Ctrl 을 누르고 놓으면 복사 */
const Drag = {
  armed: null, active: false, items: null, bytes: 0, target: null, raf: 0, cx: 0, cy: 0, tx: 0, ty: 0, suppressClick: false, copy: false,
  arm(e, it, rowEl) {
    if (e.button !== 0 || e.pointerType === 'touch') return;
    if (!selectable(it)) return;
    if (e.target.closest('.ck, .qa, button')) return;
    this.armed = { x: e.clientX, y: e.clientY, it, rowEl, id: e.pointerId };
    window.addEventListener('pointermove', this._mv = ev => this.move(ev));
    window.addEventListener('pointerup', this._up = ev => this.up(ev));
    window.addEventListener('pointercancel', this._cc = () => this.cancel());
  },
  move(e) {
    if (!this.armed) return;
    if (!this.active) {
      if (Math.hypot(e.clientX - this.armed.x, e.clientY - this.armed.y) < 6) return;
      this.start(e);
      if (!this.active) return;
    }
    this.tx = e.clientX; this.ty = e.clientY;
    this.setCopy(e.ctrlKey || e.metaKey);
    this.hitTest(e.clientX, e.clientY);
  },
  start(e) {
    const it = this.armed.it;
    const items = S.sel.has(it.key) ? Sel.items() : [it];
    this.items = items.filter(selectable);
    if (!this.items.length) { this.cleanup(); return; }
    this.active = true;
    this.bytes = sum(this.items.map(i => i.size));
    this.copy = !!(e.ctrlKey || e.metaKey);
    Tip.hide();
    Pop.closeAll();
    document.body.classList.add('dragging');
    window.getSelection && window.getSelection().removeAllRanges();
    const chip = $('#dragChip');
    chip.className = '';
    chip.innerHTML = '<div class="dcb">' + (this.items.length > 1 ? '<span class="stack"></span>' : '') + `<span class="dic">${ic('move')}</span>` +
      '<span class="dmode"></span>' + `<span class="num dsz">${fmtNum(this.items.length)}개 · ${fmtBytes(this.bytes)}</span></div>`;
    this.icon = '';
    this.paintMode();
    // 휴지통이 없는 위치의 항목이면 드롭존이 「영구 삭제」를 말한다
    const rm = Tray.recycleMode(this.items);
    this.noBin = rm === 'delete';
    $('#bzDropS').textContent = rm === 'delete' ? '영구 삭제해요(복구 불가)' : rm === 'mixed' ? '휴지통 있는 항목만 보내요' : '휴지통으로 보내요';
    $('#binZone').classList.toggle('nuke', rm === 'delete');
    this.cx = this.tx = e.clientX; this.cy = this.ty = e.clientY;
    this.place();
    raf2(() => chip.classList.add('show'));
    const srcL = new Set(this.items.map(i => letterOfPath(i.path)));
    this.srcL = srcL;
    $$('.rl[data-l]').forEach(r => {
      const L = r.dataset.l;
      if (L === '*' || r.classList.contains('off')) return;
      r.classList.add('drop');
      r.classList.toggle('same', srcL.size === 1 && srcL.has(L));
    });
    this.items.forEach(i => { const r = REG.byKey.get(i.key); if (r && r.el) r.el.classList.add('dragsrc'); });
    window.addEventListener('keydown', this._kd = ev => { if (ev.key === 'Control' || ev.key === 'Meta') this.setCopy(true); });
    window.addEventListener('keyup', this._ku = ev => { if (ev.key === 'Control' || ev.key === 'Meta') this.setCopy(false); });
    const loop = () => {
      const k = reduced ? 1 : 0.34;
      this.cx += (this.tx - this.cx) * k;
      this.cy += (this.ty - this.cy) * k;
      this.place();
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  },
  setCopy(on) {
    on = !!on;
    if (!this.active || this.copy === on) return;
    this.copy = on;
    this.paintMode();
    const t = this.target; this.target = null;
    if (t) { t.el.classList.remove('over', 'nospace'); const cap = t.el.querySelector('.rl-cap'); if (cap) cap.textContent = ''; }
    this.hitTest(this.tx, this.ty);
  },
  paintMode() {
    const chip = $('#dragChip');
    chip.classList.toggle('copy', this.copy);
    document.body.classList.toggle('dragcopy', this.copy);
    const m = $('#dragChip .dmode');
    if (m) m.textContent = this.copy ? '복사 +' : '이동';
    if (!this.target || this.target.type !== 'bin') this.setIcon(this.copy ? 'copy' : 'move');
  },
  place() {
    const chip = $('#dragChip');
    const dcb = chip.firstChild;
    const w = dcb ? dcb.offsetWidth : 140, hh = dcb ? dcb.offsetHeight : 34;
    let x = this.cx + 14, y = this.cy + 12;
    if (y + hh > innerHeight - 6) y = this.cy - hh - 10;
    if (x + w > innerWidth - 6) x = this.cx - w - 10;
    chip.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  },
  setIcon(name) {
    if (this.icon === name) return;
    this.icon = name;
    const d = $('#dragChip .dic');
    if (d) d.innerHTML = ic(name);
  },
  hitTest(x, y) {
    const el = document.elementFromPoint(x, y);
    const rl = el && el.closest ? el.closest('.rl[data-l]') : null;
    const bz = el && el.closest ? el.closest('#binZone') : null;
    let tgt = null;
    if (rl && rl.dataset.l !== '*' && !rl.classList.contains('off')) tgt = { type: 'drive', L: rl.dataset.l, el: rl };
    else if (bz) tgt = { type: 'bin', el: bz };
    if ((tgt && tgt.el) === (this.target && this.target.el)) return;
    if (this.target) {
      this.target.el.classList.remove('over', 'nospace');
      const cap = this.target.el.querySelector('.rl-cap');
      if (cap) cap.textContent = '';
    }
    this.target = tgt;
    const chip = $('#dragChip');
    chip.classList.remove('danger', 'bad');
    this.setIcon(tgt && tgt.type === 'bin' ? 'trash' : this.copy ? 'copy' : 'move');
    const m = $('#dragChip .dmode');
    if (m) m.textContent = tgt && tgt.type === 'bin' ? (this.noBin ? '영구 삭제' : '휴지통') : this.copy ? '복사 +' : '이동';
    if (!tgt) return;
    tgt.el.classList.add('over');
    if (tgt.type === 'bin') { chip.classList.add('danger'); return; }
    const d = driveOf(tgt.L);
    const same = tgt.el.classList.contains('same');
    const after = (d ? d.free : 0) - this.bytes;
    const cap = tgt.el.querySelector('.rl-cap');
    tgt.same = same;
    if (same) {
      tgt.enough = false;
      chip.classList.add('bad');
      if (cap) cap.textContent = this.copy ? '폴더를 골라 복사해요' : '같은 드라이브예요';
      if (this.copy) { tgt.pickSame = true; chip.classList.remove('bad'); }
      return;
    }
    tgt.enough = after >= MARGIN;
    if (!tgt.enough) {
      tgt.el.classList.add('nospace');
      chip.classList.add('bad');
      if (cap) cap.textContent = '공간이 부족해요';
    } else if (cap) cap.textContent = `${this.copy ? '복사' : '이동'} 후 여유 ${fmtBytes(after)}`;
  },
  up(e) {
    if (!this.armed) return;
    if (!this.active) { this.cleanup(); return; }
    this.suppressClick = true;
    setTimeout(() => { this.suppressClick = false; }, 50);
    const tgt = this.target;
    const items = this.items;
    const copy = this.copy || !!(e && (e.ctrlKey || e.metaKey));
    const dcb = $('#dragChip .dcb');
    const chipRect = dcb ? dcb.getBoundingClientRect() : null;
    this.cleanup(!!(tgt && (tgt.type === 'bin' || tgt.enough || tgt.pickSame)));
    if (!tgt) return;
    if (tgt.type === 'bin') { confirmRecycle(items); return; }
    if (tgt.same) {
      const d = driveOf(tgt.L);
      if (copy && d) {
        MovePop.items = items; MovePop.mode = 'copy'; MovePop.anchor = null;
        pickFolderNative(d.root).then(r => {
          if (r.canceled) return;
          if (r.inline) { MovePop.open(tgt.el, items, { mode: 'copy', place: 'bottom-start' }); MovePop.pick(d.root); return; }
          confirmMove(items, null, { mode: 'copy', destDir: r.path });
        }).catch(err => Toast.err(err, '폴더 선택 창을 열지 못했어요'));
      } else Toast.show({ kind: 'info', title: '같은 드라이브로는 옮길 수 없어요.', sub: 'Ctrl 을 누른 채 놓으면 그 드라이브 안의 다른 폴더에 복사할 수 있어요.' });
      return;
    }
    if (!tgt.enough) {
      const d = driveOf(tgt.L);
      Toast.show({ kind: 'err', title: `${dname(d)}에 공간이 부족해요`, sub: `${fmtBytes(this.bytes + MARGIN)} 필요(여유 1GB 포함), ${fmtBytes(d ? d.free : 0)} 남아 있어요.` });
      return;
    }
    confirmMove(items, tgt.L, { fromRect: chipRect, mode: copy ? 'copy' : 'move' });
  },
  cancel() { if (this.active) this.cleanup(false); else this.cleanup(); },
  cleanup(dropped) {
    window.removeEventListener('pointermove', this._mv);
    window.removeEventListener('pointerup', this._up);
    window.removeEventListener('pointercancel', this._cc);
    if (this._kd) { window.removeEventListener('keydown', this._kd); window.removeEventListener('keyup', this._ku); this._kd = this._ku = null; }
    cancelAnimationFrame(this.raf);
    const chip = $('#dragChip');
    if (this.active) {
      if (!dropped && this.armed && this.armed.rowEl && this.armed.rowEl.isConnected && !reduced && chip.animate) {
        const r = this.armed.rowEl.getBoundingClientRect();
        const from = chip.style.transform;
        chip.animate([{ transform: from, opacity: 1 }, { transform: `translate(${r.left + 60}px, ${r.top + 12}px)`, opacity: 0 }], { duration: 320, easing: 'cubic-bezier(.4,0,.2,1)' });
      }
      chip.classList.remove('show');
    }
    if (this.target) { this.target.el.classList.remove('over', 'nospace'); const cap = this.target.el.querySelector('.rl-cap'); if (cap) cap.textContent = ''; }
    $$('.rl.drop, .rl.same').forEach(r => r.classList.remove('drop', 'same'));
    $$('.dragsrc').forEach(r => r.classList.remove('dragsrc'));
    $('#binZone').classList.remove('nuke');
    document.body.classList.remove('dragging', 'dragcopy');
    this.armed = null; this.active = false; this.target = null; this.items = null; this.copy = false;
  },
};

/* ═════════════════════════════════════════════ 작업(잡) */
const VERB = { recycle: '휴지통으로 보내는 중', move: '이동 중', copy: '복사 중', delete: '삭제 중', empty_bin: '휴지통 비우는 중', undo: '되돌리는 중', verify_dupes: '중복 확인 중', rescan: '다시 스캔하는 중' };
const Jobs = {
  doneFlashT: 0, panel: null,
  track(id, ctx) {
    S.my.set(id, ctx || {});
    if (!S.jobSeen.has(id)) S.jobSeen.set(id, 'queued');
    Poll.kick();
  },
  update(list) {
    for (const j of list) {
      const prev = S.jobSeen.get(j.id);
      S.jobSeen.set(j.id, j.status);
      if (prev && !TERM(prev) && TERM(j.status)) this.finished(j);
    }
    this.pill(list);
    if (this.panel && !this.panel.p.closed) this.panel.update(list);
  },
  pill(list) {
    const pill = $('#jobPill');
    const act = list.filter(j => j.status === 'running' || j.status === 'queued');
    const run = act.find(j => j.status === 'running') || act[0];
    const ring = $('.jp-ring .pr', pill);
    if (run) {
      const p = run.bytes_total ? run.bytes_done / run.bytes_total : run.files_total ? run.files_done / run.files_total : 0;
      const pct = Math.floor(clamp(p, 0, 1) * 100);
      const C = 2 * Math.PI * 8;
      ring.style.strokeDasharray = C.toFixed(2);
      ring.style.strokeDashoffset = (C * (1 - clamp(p, 0.02, 1))).toFixed(2);
      const more = act.length > 1 ? ` · 대기 ${act.length - 1}` : '';
      $('#jpT').textContent = `${VERB[run.type] || '작업 중'} ${run.status === 'queued' ? '' : pct + '%'}${more}`.replace('  ', ' ');
      pill.classList.add('active');
      pill.classList.remove('done');
      pill.removeAttribute('data-tip');
      clearTimeout(this.doneFlashT);
    } else if (pill.classList.contains('active')) {
      pill.classList.remove('active');
      pill.classList.add('done');
      $('#jpT').textContent = '완료';
      clearTimeout(this.doneFlashT);
      this.doneFlashT = setTimeout(() => { pill.classList.remove('done'); $('#jpT').textContent = ''; pill.setAttribute('data-tip', '작업 기록'); }, 2600);
    }
  },
  finished(j) {
    const ctx = S.my.get(j.id);
    if (ctx && ctx.keys) ctx.keys.forEach(k => S.pendKeys.delete(k));
    if (ctx && ctx.keys && j.type === 'copy') markCopying(ctx.keys, false);
    const results = j.results || [];
    const okPaths = results.filter(r => r.ok).map(r => r.path);
    const badPaths = results.filter(r => !r.ok).map(r => r.path);
    const okKeys = okPaths.map(p => REG.byPath.get(pk(p))).filter(Boolean);
    const badKeys = new Set(badPaths.map(p => REG.byPath.get(pk(p))).filter(Boolean));
    if (ctx && ctx.keys) ctx.keys.forEach(k => { if (!okKeys.includes(k)) badKeys.add(k); });
    if (j.type === 'copy') {
      // 복사: 원본 행은 그대로 — 짧은 라임 펄스만(SPEC-2 F)
      pulseRows(okKeys, 'copied');
      if (S.screen && S.screen.onCopied) S.screen.onCopied(okPaths);
    }
    if (j.type === 'recycle' || j.type === 'move' || j.type === 'undo' || j.type === 'delete') {
      if (okKeys.length && S.screen && S.screen.removeKeys) S.screen.removeKeys(okKeys, j.type === 'recycle' || j.type === 'delete' ? 'recycled' : 'moved');
      badKeys.forEach(k => {
        const r = REG.byKey.get(k);
        if (!r) return;
        r.it._pending = false;
        if (r.el) { r.el.classList.remove('pending'); r.el.classList.remove('failed'); void r.el.offsetWidth; if (j.status !== 'canceled') r.el.classList.add('failed'); }
      });
    }
    // 이 화면에서 시작했거나, 진행 중인 것을 지켜보던 작업이면 알린다
    this.toast(j, ctx || {}, okPaths);
    Recs.load();
    ProjCount.load();
    if (j.type === 'verify_dupes') { if (S.screen && S.screen.reload) S.screen.reload(); return; }
    setTimeout(() => { if (S.screen && S.screen.refresh) S.screen.refresh('job'); }, okKeys.length && j.type !== 'copy' ? 420 : 0);
  },
  toast(j, ctx, okPaths) {
    const n = okPaths.length;
    if (j.status === 'canceled') {
      const m = (j.message || '').trim();
      Toast.show({ kind: 'info', title: '작업을 취소했어요', sub: m && m !== '취소했어요.' ? m : null });
      return;
    }
    if (j.status === 'failed') { Toast.show({ kind: 'err', title: (j.title || '작업') + ' — 실패', sub: j.message || null, actions: [{ label: '자세히', onClick: () => Jobs.openPanel() }] }); return; }
    const partial = j.status === 'partial';
    const freed = j.freed || {}, added = j.added || {};
    const freedSum = sum(Object.values(freed));
    if (j.type === 'recycle') {
      const ls = Object.keys(freed);
      Toast.show({
        kind: partial ? 'err' : 'ok',
        title: partial ? `${fmtNum(n)}개만 휴지통으로 보냈어요` : `${fmtNum(n)}개 항목(${fmtBytes(freedSum)})을 휴지통으로 보냈어요`,
        sub: partial ? j.message : `휴지통을 비워야 ${ls.map(L => L + ':').join(', ')} 공간이 생겨요. 되돌리려면 휴지통에서 복원하세요.`,
        actions: ls.length ? [{ label: '휴지통 비우기', primary: true, onClick: () => confirmEmptyBin(ls) }] : [],
      });
    } else if (j.type === 'move') {
      const T = Object.keys(added)[0] || ctx.target;
      const tn = dname(driveOf(T)) || T;
      Toast.show({
        kind: partial ? 'err' : 'lime',
        title: partial ? '일부만 옮겼어요' : `${fmtBytes(sum(Object.values(added)) || j.bytes_total)}를 ${tn}${ro(tn)} 옮겼어요`,
        sub: j.message || null,
        actions: j.undoable ? [{ label: '되돌리기', primary: true, onClick: () => Jobs.undo(j.id) }] : [],
        timeout: 10000,
      });
    } else if (j.type === 'copy') {
      const T = Object.keys(added)[0] || j.target || ctx.target;
      const td = driveOf(T);
      const first = (j.results || []).find(r => r.ok && r.dest);
      const acts = [];
      if (first) acts.push({ label: '열기', onClick: () => reveal(first.dest) });
      if (j.undoable) acts.push({ label: '되돌리기', primary: true, onClick: () => Jobs.undo(j.id) });
      Toast.show({
        kind: partial ? 'err' : 'lime',
        title: partial ? '일부만 복사했어요' : `복사했어요 · ${fmtBytes(sum(Object.values(added)) || j.bytes_total)} → ${td ? (dtag(td) || dlabel(td)) : (j.target_label || '')}`,
        sub: j.message || (partial ? null : '원본은 그대로 있어요.'),
        actions: acts,
        timeout: 10000,
      });
    } else if (j.type === 'delete') {
      Toast.show({ kind: partial ? 'err' : 'ok', title: partial ? `${fmtNum(n)}개만 지웠어요` : `${fmtNum(n)}개 항목(${fmtBytes(freedSum || j.bytes_total)})을 영구 삭제했어요`, sub: j.message || null });
    } else if (j.type === 'undo') {
      Toast.show({ kind: 'ok', title: '되돌렸어요', sub: j.message || null });
    } else if (j.type === 'empty_bin') {
      Toast.show({ kind: 'ok', title: `휴지통을 비워 ${fmtBytes(freedSum)}를 확보했어요`, sub: Object.keys(freed).map(L => `${L}: ${fmtBytes(freed[L])}`).join(' · ') });
    } else {
      Toast.show({ kind: partial ? 'err' : 'ok', title: j.message || '작업을 마쳤어요.' });
    }
  },
  async undo(id, force, jobObj) {
    const j = jobObj || S.jobs.find(x => x.id === id);
    if (!force && j && j.type === 'copy') {
      const td = driveOf(j.target);
      if (td && !hasBin(td)) { confirmUndoCopy(j, td); return; }
    }
    try {
      const r = await POST('/api/ops/undo', force ? { job_id: id, confirm: true } : { job_id: id });
      Jobs.track(r.job_id, { kind: 'undo', undo_of: id });
    } catch (e) {
      // 화면이 몰랐던 「휴지통 없음」 — 영구 삭제가 되니 확인창부터
      if (e.code === 'need_confirm' && j && j.type === 'copy') { confirmUndoCopy(j, driveOf(j.target) || { label: '복사한 위치' }); return; }
      if (e.code === 'drive_changed') { Toast.show({ kind: 'err', title: '되돌리지 못했어요', sub: e.message + ' 원래 디스크를 다시 연결한 뒤 되돌려 주세요. 되돌리기 버튼은 그대로 남아 있어요.', timeout: 9000 }); return; }
      Toast.err(e, '되돌리지 못했어요');
    }
  },
  async cancel(id) {
    try { await POST('/api/ops/cancel', { job_id: id }); Poll.kick(); } catch (e) { Toast.err(e, '취소하지 못했어요'); }
  },
  openPanel() {
    if (this.panel && !this.panel.p.closed) { Pop.close(this.panel.p); return; }
    this.panel = JobPanel($('#jobPill'));
  },
};

/* 휴지통 없는 곳에 만든 복사본 되돌리기 = 영구 삭제 → 한 번 더 묻는다 */
function confirmUndoCopy(j, td) {
  const go = h('button', { class: 'btn solid-danger', type: 'button', html: ic('undo') + '<span>복사본 지우기</span>', disabled: true });
  const cancel = h('button', { class: 'btn ghost', type: 'button', text: '취소' });
  const body = h('div', {},
    h('div', { class: 'stats3' }, stat('복사한 크기', fmtBytes(j.bytes_done || j.bytes_total)), stat('파일', fmtNum(j.files_done || j.files_total) + '개'), stat('위치', dname(td))),
    note('danger', 'warn', h('b', { text: '복사본을 영구 삭제해요. ' }), `${dname(td)}에는 휴지통이 없어요. 이 작업이 만든 파일·폴더만 지우고, 원본은 그대로 둬요.`));
  let ok = false;
  body.append(checkRow('복사본이 되돌릴 수 없게 지워진다는 것을 확인했어요', v => { ok = v; go.disabled = !v; }));
  const sh = Sheet.open({ icon: h('span', { class: 'tile t-lg danger-tile', html: ic('undo') }), title: '복사를 되돌릴까요?', sub: j.title || '', body, cls: 'danger' });
  sh.primary = go;
  sh.setFoot([h('span', { class: 'sp' }), cancel, go]);
  cancel.addEventListener('click', () => sh.close());
  go.addEventListener('click', () => { if (!ok) return; sh.close(); Jobs.undo(j.id, true, j); });
}

function JobPanel(anchor) {
  const list = h('div', { class: 'scroll' });
  const bd = h('div', { class: 'bd' }, list);
  const more = h('button', { class: 'link', type: 'button', html: '전체 기록' + ic('chevR') });
  const box = h('div', { class: 'jobpanel' }, h('div', { class: 'hd' }, h('h4', { text: '작업' }), more), bd);
  const p = Pop.open(anchor, box, { cls: '', place: 'bottom-end' });
  attachScrollbar(list, bd);
  let all = null;
  const items = new Map();
  more.addEventListener('click', async () => {
    try { const r = await GET('/api/jobs'); all = r.jobs || []; render(S.jobs); more.hidden = true; } catch (e) { Toast.err(e); }
  });
  function itemEl(j) {
    const st = h('span', { class: 'st ' + j.status });
    const t = h('span', { class: 't' });
    const w = h('span', { class: 'w num' });
    const msg = h('div', { class: 'msg' });
    const prog = h('div', { class: 'prog' }, h('i'));
    const meta = h('div', { class: 'meta num' });
    const cur = h('div', { class: 'cur' }, h('bdi'));
    const acts = h('div', { class: 'acts' });
    const el = h('div', { class: 'jitem' }, h('div', { class: 'l1' }, st, t, w), msg, prog, meta, cur, acts);
    return { el, st, t, w, msg, prog, meta, cur, acts, sig: '' };
  }
  function fill(x, j) {
    const act = j.status === 'running' || j.status === 'queued';
    x.st.className = 'st ' + j.status;
    const icn = { done: 'check', partial: 'warn', failed: 'x', canceled: 'minus' }[j.status];
    const sig = j.status + '|' + (j.undoable ? 1 : 0);
    if (x.sig !== sig) {
      x.sig = sig;
      x.st.innerHTML = act ? ic('spinner', j.status === 'running' ? 'spin' : '') : ic(icn);
      x.acts.textContent = '';
      if (act) x.acts.append(h('button', { class: 'btn sm', type: 'button', html: ic('stop') + '<span>취소</span>', onclick: () => Jobs.cancel(j.id) }));
      else if (j.undoable && (j.type === 'move' || j.type === 'copy')) x.acts.append(h('button', { class: 'btn sm', type: 'button', html: ic('undo') + `<span>${j.type === 'copy' ? '되돌리기(복사본 지우기)' : '되돌리기'}</span>`, onclick: () => Jobs.undo(j.id, false, j) }));
      x.acts.hidden = !x.acts.childNodes.length;
    }
    x.t.textContent = j.title || '작업';
    x.w.textContent = act ? (j.status === 'queued' ? '대기 중' : '') : relTime(j.finished_at || j.created_at);
    x.w.setAttribute('data-tip', absTime(j.finished_at || j.created_at));
    x.msg.textContent = act ? '' : (j.message || '');
    x.msg.hidden = act || !j.message;
    x.prog.hidden = !act;
    x.meta.hidden = !act;
    x.cur.hidden = !act || !j.current;
    if (act) {
      const p = j.bytes_total ? j.bytes_done / j.bytes_total : j.files_total ? j.files_done / j.files_total : 0;
      x.prog.classList.toggle('indet', j.status === 'queued' || (!j.bytes_total && !j.files_total));
      x.prog.firstChild.style.width = (clamp(p, 0, 1) * 100).toFixed(1) + '%';
      const parts = [];
      if (j.bytes_total) parts.push(`<b>${fmtBytes(j.bytes_done)}</b> / ${fmtBytes(j.bytes_total)}`);
      if (j.speed_bps) parts.push(`${fmtBytes(j.speed_bps)}/s`);
      if (j.eta_s != null && j.status === 'running') parts.push(`남은 시간 ${fmtDur(j.eta_s)}`);
      parts.push(`${Math.floor(clamp(p, 0, 1) * 100)}%`);
      x.meta.innerHTML = parts.join(' · ');
      x.cur.firstChild.textContent = j.current ? fmtPath(j.current, 60) : '';
    }
  }
  function render(curJobs) {
    const byId = new Map();
    (all || []).forEach(j => byId.set(j.id, j));
    (curJobs || []).forEach(j => byId.set(j.id, j));
    const js = [...byId.values()];
    const act = js.filter(j => j.status === 'running' || j.status === 'queued').sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
    const fin = js.filter(j => TERM(j.status)).sort((a, b) => (b.finished_at || b.created_at || 0) - (a.finished_at || a.created_at || 0));
    const sig = act.map(j => j.id).join(',') + '/' + fin.map(j => j.id).join(',');
    if (list._sig !== sig) {
      list._sig = sig;
      list.textContent = '';
      if (act.length) list.append(h('div', { class: 'grp', text: '진행 중' }));
      act.forEach(j => { let x = items.get(j.id); if (!x) { x = itemEl(j); items.set(j.id, x); } list.append(x.el); });
      if (fin.length) list.append(h('div', { class: 'grp', text: '최근 기록' }));
      fin.forEach(j => { let x = items.get(j.id); if (!x) { x = itemEl(j); items.set(j.id, x); } list.append(x.el); });
      if (!act.length && !fin.length) list.append(h('div', { class: 'empty', style: { padding: '30px 10px' } }, h('p', { text: '아직 작업 기록이 없어요.' })));
    }
    [...act, ...fin].forEach(j => fill(items.get(j.id), j));
    Pop.place(p);
  }
  render(S.jobs);
  return { p, update: render };
}

/* ═════════════════════════════════════════════ 추천 */
const Recs = {
  t: 0,
  load() {
    clearTimeout(this.t);
    this.t = setTimeout(async () => {
      if (!S.drives.some(d => d.scanned)) { S.recs = []; S.recsVer++; S.screen && S.screen.onRecs && S.screen.onRecs(); return; }
      try {
        const r = await GET('/api/recommendations');
        S.recs = r.items || [];
        S.recsVer++;
        if (S.screen && S.screen.onRecs) S.screen.onRecs();
      } catch (e) { /* 다음 기회에 */ }
    }, 250);
  },
};
const REC_ICON = { cache: ['c0', 'c0'], bin: ['bin', 'trash'], old_render: ['c1', 'clock'], dupes: ['c4', 'dupes'], big: ['c2', 'big'], old_project: ['c5', 'oldf'], balance: ['warn', 'warn'] };
const TONE = { safe: '지워도 안전', review: '확인 필요', info: '참고' };
function recAct(rec) {
  const a = rec.action || {};
  if (a.type === 'empty_bin') return confirmEmptyBin(a.drives || []);
  if (a.type === 'dupes') return go({ name: 'dupes' });
  if (a.type === 'view') {
    return go({ name: 'list', drive: a.drive || '*', view: a.view || 'files', cat: a.cat == null ? null : a.cat, age: a.age || 0, dir: a.dir || 0, q: '', sort: 'size', order: 'desc', title: rec.title });
  }
}
function recCard(rec) {
  const [c, g] = REC_ICON[rec.kind] || ['c10', 'spark'];
  const v = h('span', { class: 'num' });
  const el = h('button', { class: 'rec', type: 'button' },
    h('div', { class: 'rec-top' }, tile(c, g, 't-sm'), h('span', { class: 'tone ' + rec.tone }, h('i'), TONE[rec.tone] || '')),
    h('div', { class: 't', text: rec.title }),
    h('div', { class: 'v' }, v, rec.count ? h('small', { class: 'num', text: rec.kind === 'dupes' ? `${fmtNum(rec.count)}묶음` : rec.kind === 'balance' ? '' : `${fmtNum(rec.count)}개` }) : null),
    h('div', { class: 'd', text: rec.desc || '' }),
    h('span', { class: 'go', html: ic('arrowR') }));
  const prev = S.recPrev[rec.id];
  if (prev != null) { v._tvSet = true; v._tv = prev; }
  tw(v, rec.bytes, fmtBytes);
  S.recPrev[rec.id] = rec.bytes;
  el.addEventListener('click', () => recAct(rec));
  return el;
}

/* ═════════════════════════════════════════════ 레일 */
const Rail = {
  items: new Map(), heads: new Map(), nav: {}, sig: '',
  init() {
    const nav = $('#rnav');
    const mkNav = (id, icon, label, route) => {
      const c = h('span', { class: 'nv-c num' });
      const b = h('button', { class: 'nv', type: 'button', 'data-nav': id }, h('span', { class: 'nv-ic', html: ic(icon) }), h('span', { class: 'nv-l', text: label }), c);
      b.addEventListener('click', () => { if (Drag.suppressClick) return; go(route); });
      nav.append(b);
      this.nav[id] = { el: b, c };
    };
    mkNav('storage', 'storage', '저장 공간', { name: 'home' });
    mkNav('projects', 'proj', '프로젝트', { name: 'projects' });
    nav.append(h('span', { class: 'nv-ind' }));
    const list = $('#railList');
    this.addRow = h('button', { class: 'rl-add', type: 'button', 'data-tip': '\\\\ORICO 처럼 장치 이름만 넣으면 공유 폴더를 찾아 드려요' }, h('span', { class: 'rl-ic', html: ic('plus') }), h('span', { class: 'rl-nm', text: 'NAS 추가' }));
    this.addRow.addEventListener('click', () => openNasSheet());
    this.quick = h('div', { class: 'rail-quick' });
    list.append(this.quick);
    const bz = $('#binZone');
    $('.bz-ic', bz).innerHTML = ic('trash');
    $('#bzEmpty').addEventListener('click', e => { e.stopPropagation(); if (!Drag.suppressClick) confirmEmptyBin(); });
    bz.addEventListener('click', () => { if (!Drag.suppressClick) confirmEmptyBin(); });
    bz.addEventListener('keydown', e => { if (e.key === 'Enter') confirmEmptyBin(); });
    attachScrollbar(list, $('#rail'));
  },
  head(gid) {
    let x = this.heads.get(gid);
    if (x) return x;
    const g = GROUPS.find(g => g.id === gid);
    const n = h('span', { class: 'num' });
    const el = h('div', { class: 'rail-label', 'data-g': gid }, h('span', { text: g.label }), h('span', { class: 'sp' }), n);
    if (gid === 'net') {
      const add = h('button', { class: 'ibtn xs', type: 'button', 'data-tip': 'NAS 추가', 'aria-label': 'NAS 추가', html: ic('plus') });
      add.addEventListener('click', e => { e.stopPropagation(); openNasSheet(); });
      el.append(add);
    }
    x = { el, n };
    this.heads.set(gid, x);
    return x;
  },
  mk(L) {
    const ico = h('span', { class: 'rl-ic', html: ic('ssd') });
    const nm = h('span', { class: 'rl-nm' });
    const fr = h('span', { class: 'rl-fr num' });
    const bar = new Bar('b-xs', { tip: false });
    const cap = h('span', { class: 'rl-cap num' });
    const el = h('button', { class: 'rl', type: 'button', 'data-l': L }, ico, nm, fr, h('span', { class: 'rl-bar' }, bar.el), cap);
    el.addEventListener('click', () => {
      if (Drag.suppressClick) return;
      go({ name: 'drive', letter: L, tab: (S.route.name === 'drive' && S.route.tab) || 'cats' });
    });
    const x = { el, ico, nm, fr, bar, kind: null };
    this.items.set(L, x);
    return x;
  },
  update() {
    const list = $('#railList');
    const live = liveDrives();
    const seen = new Set();
    const order = [];
    const counts = {};
    for (const g of GROUPS) {
      const ds = S.drives.filter(d => driveGroup(d) === g.id);
      counts[g.id] = ds.length;
      if (!ds.length && g.id !== 'net') continue;
      const hd = this.head(g.id);
      hd.n.textContent = ds.length ? String(ds.length) : '';
      order.push(hd.el);
      for (const d of ds) {
        seen.add(d.letter);
        const x = this.items.get(d.letter) || this.mk(d.letter);
        order.push(x.el);
        this.paint(x, d);
      }
      if (g.id === 'net' && !ds.length) order.push(this.addRow);
    }
    for (const [L, x] of this.items) if (!seen.has(L)) { x.el.remove(); this.items.delete(L); }
    for (const [gid, x] of this.heads) if (!order.includes(x.el)) x.el.remove();
    if (!order.includes(this.addRow)) this.addRow.remove();
    // 드라이브가 적은 PC: 빈 자리에 정리 추천 바로가기
    const quick = this.quickRows(live.length <= 3);
    if (quick) order.push(this.quick);
    else this.quick.remove();
    const sig = order.map(e => e.dataset.l || e.dataset.g || e.className).join('|');
    if (sig !== this.sig) { this.sig = sig; order.forEach(e => list.append(e)); }
    const r = S.route;
    for (const [L, x] of this.items) x.el.classList.toggle('on', r.name === 'drive' && r.letter === L);
    const sec = r.name === 'projects' ? 'projects' : 'storage';
    for (const id in this.nav) this.nav[id].el.classList.toggle('on', id === sec);
    this.nav.storage.el.classList.toggle('sub', r.name !== 'home' && sec === 'storage');
    setT(this.nav.storage.c, live.length ? String(live.length) : '');
    setT(this.nav.projects.c, S.projCount == null ? '' : fmtNum(S.projCount));
    this.nav.storage.el.setAttribute('data-tip', live.length ? `드라이브 ${live.length}개 · ${fmtBytes(totals().free)} 남음` : '');
    this.binZone(totals());
  },
  paint(x, d) {
    const k = driveIcon(d);
    if (x.kind !== k) { x.ico.innerHTML = ic(k); x.kind = k; }
    const tg = dtag(d);
    const nmH = `<b>${esc(dlabel(d))}</b>${tg ? `<em>${esc(tg)}</em>` : ''}`;
    if (x.nm._h !== nmH) { x.nm.innerHTML = nmH; x.nm._h = nmH; }
    const conn = isConn(d);
    x.el.classList.toggle('off', !conn);
    x.el.classList.toggle('noscan', conn && !d.scanned && !d.scan);
    if (conn) {
      if (x.fr._off) { x.fr._off = false; x.fr._tvSet = false; }
      tw(x.fr, d.free, fmtBytes);
      x.fr.classList.toggle('low', d.total > 0 && d.free / d.total < 0.1);
      x.el.removeAttribute('data-tip');
    } else {
      x.fr._off = true;
      setT(x.fr, '연결 안 됨');
      x.fr.classList.remove('low');
      x.el.setAttribute('data-tip', d.scanned_at ? `마지막 스캔 ${relTime(d.scanned_at)} · 기록을 볼 수 있어요` : '연결되지 않은 장치예요');
    }
    x.bar.set(driveVals(d), d.total || 1);
    x.bar.setScanning(isScanning(d.letter));
    x.el.classList.toggle('scanning', isScanning(d.letter));
  },
  quickRows(want) {
    const recs = (S.recs || []).filter(r => r.bytes > 0).slice(0, 3);
    if (!want || !recs.length) return false;
    const sig = recs.map(r => r.id + ':' + r.bytes).join('|');
    if (this.quick._sig === sig) return true;
    this.quick._sig = sig;
    this.quick.textContent = '';
    this.quick.append(h('div', { class: 'rail-label' }, h('span', { text: '정리 추천' }), h('span', { class: 'sp' }),
      h('button', { class: 'link xs', type: 'button', text: '모두', onclick: () => { go({ name: 'home' }); setTimeout(() => { const r = $('.recs'); if (r) r.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' }); }, 260); } })));
    recs.forEach(rec => {
      const [c, g] = REC_ICON[rec.kind] || ['c10', 'spark'];
      const b = h('button', { class: 'rq', type: 'button' }, tile(c, g, 't-xs'), h('span', { class: 'rq-t', text: rec.title }), h('span', { class: 'rq-v num', text: fmtBytes(rec.bytes) }));
      b.addEventListener('click', () => recAct(rec));
      this.quick.append(b);
    });
    return true;
  },
  binZone(t) {
    const bz = $('#bzSub');
    const nb = liveDrives().filter(d => d.bin_bytes > 0);
    if (t.bin > 0) {
      bz.textContent = `${fmtBytes(t.bin)} 차지 중`;
      $('#binZone').setAttribute('data-tip', nb.map(d => `${dtag(d) || dlabel(d)} ${fmtBytes(d.bin_bytes)}`).join('  ·  ') + '\n휴지통으로 보낸 용량은 비워야 공간이 생겨요.');
      $('#bzEmpty').hidden = false;
    } else { bz.textContent = '비어 있어요'; $('#binZone').removeAttribute('data-tip'); $('#bzEmpty').hidden = true; }
  },
};

/* ═════════════════════════════════════════════ 상단 바 */
const Top = {
  init() {
    $('#backBtn').innerHTML = ic('back');
    $('#backBtn').addEventListener('click', () => back());
    $('#searchIc').outerHTML = ic('search');
    $('#searchClr').innerHTML = ic('x');
    $('#setBtn').innerHTML = ic('gear');
    $('#setBtn').addEventListener('click', () => openSettings());
    $('#jpIc').innerHTML = `<svg class="jp-ring" viewBox="0 0 20 20"><circle class="tr" cx="10" cy="10" r="8"/><circle class="pr" cx="10" cy="10" r="8"/></svg>${ic('clock', 'jp-clock')}${ic('check', 'jp-done')}<span class="badge-dot"></span>`;
    $('#jobPill').addEventListener('click', () => Jobs.openPanel());
    $('#updPill').addEventListener('click', () => { const u = S.update; if (u && u.url) POST('/api/open_url', { url: u.url }).then(r => { if (!r.opened) Toast.show({ kind: 'info', title: '브라우저를 열지 못했어요', sub: u.url }); }).catch(e => Toast.err(e, '릴리스 페이지를 열지 못했어요')); });
    $('#scanBtn').addEventListener('click', () => this.scanClick());
    $('#offline').innerHTML = ic('offline') + '<span>앱 서버에 연결할 수 없어요. 다시 연결하는 중…</span>';
    const inp = $('#searchIn'), box = $('#search');
    let t = 0;
    inp.addEventListener('input', () => {
      box.classList.toggle('has', !!inp.value);
      clearTimeout(t);
      if (S.route.name === 'list') t = setTimeout(() => this.search(inp.value, false), 280);
    });
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); clearTimeout(t); this.search(inp.value, true); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); if (inp.value) { inp.value = ''; box.classList.remove('has'); if (S.route.name === 'list' && S.route.q) this.search('', false); } else inp.blur(); }
    });
    $('#searchClr').addEventListener('click', () => { inp.value = ''; box.classList.remove('has'); if (S.route.name === 'list' && S.route.q) this.search('', false); inp.focus(); });
    const view = $('#view');
    view.addEventListener('scroll', () => $('#top').classList.toggle('scrolled', view.scrollTop > 4), { passive: true });
  },
  updatePill() {
    const b = $('#updPill');
    const u = S.update;
    const on = !!(u && u.newer && u.latest);
    const sig = on ? u.latest : '';
    if (b._sig === sig) return;
    b._sig = sig;
    b.hidden = !on;
    if (on) {
      b.innerHTML = `<i></i><span>새 버전 <b class="num">v${esc(String(u.latest).replace(/^v/i, ''))}</b></span>` + ic('ext2');
      b.setAttribute('data-tip', '릴리스 페이지를 기본 브라우저로 열어요. 받은 뒤 압축을 풀어 덮어쓰면 돼요.');
      b.classList.remove('pop-in'); void b.offsetWidth; b.classList.add('pop-in');
    }
  },
  scope() {
    const r = S.route;
    if (r.name === 'drive') return r.letter;
    if (r.name === 'list') return r.drive || '*';
    return '*';
  },
  search(q, commit) {
    q = q.trim();
    const r = S.route;
    if (r.name === 'list') {
      if ((r.q || '') === q) return;
      const nr = Object.assign({}, r, { q, view: q ? 'files' : r.view });
      if (!q && r.fromSearch) { back(); return; }
      S.route = nr;
      if (S.screen && S.screen.setRoute) S.screen.setRoute(nr);
      this.update();
      return;
    }
    if (!q || !commit) return;
    const L = this.scope();
    go({ name: 'list', drive: L, view: 'files', cat: null, age: 0, dir: 0, q, sort: 'size', order: 'desc', fromSearch: true });
  },
  scanClick() {
    const live = liveDrives();
    const act = live.filter(d => isScanning(d.letter));
    if (act.length) {
      const items = [{ head: '스캔 중인 드라이브' }];
      act.forEach(d => items.push({ label: dname(d), icon: driveIcon(d), right: '취소', onClick: () => cancelScan(d.letter) }));
      const rest = live.filter(d => !isScanning(d.letter) && d.scan);
      if (rest.length) { items.push('-'); items.push({ label: `나머지 ${rest.length}개도 스캔`, icon: 'scan', onClick: () => startScan(rest.map(d => d.letter)) }); }
      Menu.open($('#scanBtn'), items, { place: 'bottom-end' });
      return;
    }
    const off = live.filter(d => !d.scan);
    if (off.length && !live.some(d => d.scan)) { Menu.open($('#scanBtn'), [{ head: '스캔할 드라이브를 고르세요' }, ...off.map(d => ({ label: dname(d), icon: driveIcon(d), onClick: () => startScan([d.letter]) }))], { place: 'bottom-end' }); return; }
    startScan('all');
  },
  update() {
    const r = S.route;
    const cr = $('#crumbs');
    cr.textContent = '';
    const parts = [{ label: '저장 공간', route: { name: 'home' } }];
    if (r.name === 'drive') parts.push({ label: dname(driveOf(r.letter)) || r.letter });
    if (r.name === 'list') {
      if (r.drive && r.drive !== '*') parts.push({ label: dname(driveOf(r.drive)), route: { name: 'drive', letter: r.drive, tab: 'cats' } });
      else parts.push({ label: '모든 드라이브' });
      parts.push({ label: listTitle(r) });
    }
    if (r.name === 'dupes') parts.push({ label: '중복 파일' });
    if (r.name === 'projects') { parts.length = 0; parts.push({ label: '프로젝트' }); }
    parts.forEach((p, i) => {
      if (i) cr.append(icEl('chevR', 'sep'));
      const last = i === parts.length - 1;
      const b = h('button', { type: 'button', class: last ? 'cur' : '', text: p.label });
      if (!last && p.route) b.addEventListener('click', () => go(p.route));
      else if (!last) b.style.pointerEvents = 'none';
      cr.append(b);
    });
    $('#backBtn').hidden = r.name === 'home' || r.name === 'projects' || !(S.stack.length || (S.screen && S.screen.canUp && S.screen.canUp()));
    const L = this.scope();
    const inp = $('#searchIn');
    inp.placeholder = L === '*' ? '모든 드라이브에서 검색' : `${dname(driveOf(L)) || L}에서 검색`;
    if (r.name === 'list') { if (document.activeElement !== inp) inp.value = r.q || ''; }
    else if (document.activeElement !== inp) inp.value = '';
    $('#search').classList.toggle('has', !!inp.value);
    $('#search').hidden = r.name === 'projects';
    this.scanBtn();
    this.updatePill();
  },
  scanBtn() {
    const b = $('#scanBtn');
    const live = liveDrives();
    const act = live.filter(d => isScanning(d.letter));
    const tg = live.filter(d => d.scan || isScanning(d.letter));
    const skip = live.filter(d => !d.scan).length;
    const sig = act.length + '/' + tg.length + '/' + skip;
    if (b._sig === sig) return;
    b._sig = sig;
    if (act.length) {
      b.innerHTML = ic('spinner', 'spin') + `<span class="num">스캔 중 ${act.length}/${Math.max(act.length, tg.length)}</span>`;
      b.setAttribute('data-tip', '누르면 스캔을 취소할 수 있어요');
    } else {
      b.innerHTML = ic('scan') + '<span>모두 스캔</span>';
      b.setAttribute('data-tip', `스캔 대상 드라이브 ${tg.length}개를 다시 스캔해요${skip ? `\n외장·NAS 등 ${skip}개는 빼요(설정에서 바꿀 수 있어요)` : ''}\nF5 는 화면만 새로고침해요`);
    }
  },
};
async function startScan(drives) {
  try {
    const r = await POST('/api/scan', { drives });
    const one = r.started && r.started.length === 1 ? driveOf(r.started[0]) : null;
    const net = one && one.kind === 'network';
    if (r.started && r.started.length) Toast.show({ kind: 'info', title: r.started.length > 1 ? `드라이브 ${r.started.length}개를 스캔해요` : `${dname(one)} 스캔을 시작했어요`, sub: net ? '네트워크라 시간이 걸려요. 진행 중 속도를 보여 드리고, 언제든 취소할 수 있어요.' : 'HDD 는 시간이 조금 걸려요. 스캔 중에도 앱을 쓸 수 있어요.' });
    else if (r.skipped && r.skipped.length) Toast.show({ kind: 'info', title: '스캔할 드라이브가 없어요', sub: r.skipped.map(x => x.reason).filter(Boolean)[0] || null });
    Poll.kick();
  } catch (e) { Toast.err(e, '스캔을 시작하지 못했어요'); }
}
async function cancelScan(L) {
  try { await POST('/api/scan/cancel', { drive: L }); Poll.kick(); } catch (e) { Toast.err(e); }
}

/* ═════════════════════════════════════════════ 라우터 */
function go(route, o = {}) {
  Pop.closeAll();
  if (!o.replace) S.stack.push(S.route);
  if (S.stack.length > 50) S.stack.shift();
  S.route = route;
  mount(o.back ? 'bwd' : 'fwd');
}
function back() {
  if (S.screen && S.screen.canUp && S.screen.canUp()) { S.screen.up(); Top.update(); return; }
  if (!S.stack.length) { if (S.route.name !== 'home') { S.route = { name: 'home' }; mount('bwd'); } return; }
  S.route = S.stack.pop();
  Pop.closeAll();
  mount('bwd');
}
function mount(dir = 'fwd') {
  Sel.clear();
  Tip.hide();
  HL.set(null);
  const view = $('#view');
  const old = S.screen;
  if (old) {
    if (old.destroy) old.destroy();
    const top = view.scrollTop;
    old.el.classList.remove('enter');
    if (!reduced) {
      old.el.style.top = (-top) + 'px';
      old.el.classList.add('leave');
      setTimeout(() => old.el.remove(), 150);
    } else old.el.remove();
  }
  regClear();
  const r = S.route;
  const ikey = r.name === 'drive' ? 'drive:' + r.letter : r.name;
  S.introNow = !S.seen.has(ikey);
  let sc;
  if (r.name === 'drive' && driveOf(r.letter)) sc = DriveScreen(r);
  else if (r.name === 'list') sc = ListScreen(r);
  else if (r.name === 'dupes') sc = DupesScreen();
  else if (r.name === 'projects') sc = ProjectsScreen();
  else { S.route = { name: 'home' }; sc = HomeScreen(); }
  S.screen = sc;
  sc.el._intro = S.introNow;
  S.seen.add(r.name === 'drive' ? 'drive:' + r.letter : S.route.name);
  sc.el.classList.add('screen', 'enter', dir);
  view.append(sc.el);
  view.scrollTop = 0;
  setTimeout(() => sc.el.classList.remove('enter', 'fwd', 'bwd'), 260);
  if (sc.update) sc.update();
  Rail.update();
  Top.update();
}

/* ═════════════════════════════════════════════ 홈 */
/* 스위치(켜면 바로 적용되는 설정에만 쓴다) */
function Switch(on, onChange, o = {}) {
  const el = h('button', { class: 'sw' + (on ? ' on' : ''), type: 'button', role: 'switch', 'aria-checked': on ? 'true' : 'false', 'aria-label': o.label || null, 'data-tip': o.tip || null }, h('i'));
  let cur = !!on, busy = false;
  const set = v => { cur = !!v; el.classList.toggle('on', cur); el.setAttribute('aria-checked', cur ? 'true' : 'false'); };
  el.addEventListener('click', async e => {
    e.stopPropagation();
    if (busy) return;
    const nv = !cur;
    set(nv);
    busy = true;
    try { const ok = await onChange(nv); if (ok === false) set(!nv); } catch (err) { set(!nv); Toast.err(err, '바꾸지 못했어요'); } finally { busy = false; }
  });
  return { el, set, get value() { return cur; } };
}
async function setScanTarget(L, on) {
  const r = await POST('/api/drives/scan_toggle', { key: L, scan: on });
  if (r.settings) S.settings = r.settings;
  const d = driveOf(L);
  if (d) d.scan = on;
  Toast.show({ kind: 'ok', title: on ? `${dname(d)}도 「모두 스캔」에 넣었어요` : `${josa(dname(d), '은', '는')} 「모두 스캔」에서 뺐어요`, sub: on ? null : '카드의 [스캔] 으로 따로 스캔할 수 있어요.', timeout: 3200 });
  Poll.kick();
  return true;
}
async function confirmForget(d) {
  const go = h('button', { class: 'btn danger', type: 'button', html: ic('trash') + '<span>기록 지우기</span>' });
  const cancel = h('button', { class: 'btn ghost', type: 'button', text: '취소' });
  const sh = Sheet.open({
    icon: tile('drive', driveIcon(d), 't-lg'), title: `${dname(d)}의 기록을 지울까요?`,
    sub: d.scanned_at ? `마지막 스캔 ${relTime(d.scanned_at)}` : '',
    body: h('div', {}, note('', 'info', h('b', { text: '저장해 둔 스캔 결과만 지워요. ' }), '장치 안의 파일은 건드리지 않아요. 다시 연결하고 스캔하면 다시 볼 수 있어요.')),
  });
  sh.primary = go;
  sh.setFoot([h('span', { class: 'sp' }), cancel, go]);
  cancel.addEventListener('click', () => sh.close());
  go.addEventListener('click', async () => {
    go.disabled = true; sh.busy = true;
    try {
      await POST('/api/drives/forget', { key: d.letter });
      sh.busy = false; sh.close();
      Toast.show({ kind: 'ok', title: `${dname(d)} 기록을 지웠어요` });
      if (S.route.name === 'drive' && S.route.letter === d.letter) go_home();
      Poll.kick();
    } catch (e) { sh.busy = false; go.disabled = false; sh.shake(); sh.body.append(note('danger', 'warn', e.message)); }
  });
}
const go_home = () => go({ name: 'home' }, { replace: true });

function DriveCard(d0, o = {}) {
  const L = d0.letter;
  const icoBox = h('span', { class: 'dc-ic' });
  const nm = h('span', { class: 'dc-nm' });
  // 이름 툴팁은 글자 수가 아니라 실제로 잘렸을 때만 단다(포인터가 올라오는 순간 판정 — Tip 보다 먼저 돈다)
  nm.addEventListener('pointerover', () => { if (nm._full && nm.scrollWidth > nm.clientWidth + 1) nm.setAttribute('data-tip', nm._full); else nm.removeAttribute('data-tip'); });
  const kchip = h('span', { class: 'kchip' });
  const nbchip = h('span', { class: 'kchip nb', text: '휴지통 없음', 'data-tip': '이 위치는 휴지통이 없어요. 지우면 바로 영구 삭제돼요.' });
  const scanB = h('button', { class: 'ibtn sm scanbtn', type: 'button', 'data-tip': '다시 스캔', html: ic('scan') });
  const free = h('span', { class: 'num' });
  const freeL = h('small', { text: '남음' });
  const freeBox = h('div', { class: 'dc-free' }, free, freeL);
  const used = h('span', { class: 'dc-used num' });
  const bar = new Bar(o.big ? 'b-md' : 'b-sm');
  const catsBox = o.big ? h('div', { class: 'dc-cats' }) : null;
  const foot = h('div', { class: 'dc-foot' });
  const el = h('div', { class: 'dcard' + (o.big ? ' big' : ''), role: 'button', tabindex: '0', 'data-l': L },
    h('div', { class: 'dc-head' }, icoBox, h('div', { class: 'dc-nmw' }, nm), kchip, nbchip, h('span', { class: 'sp' }), scanB),
    h('div', { class: 'dc-nums' }, freeBox, used), bar.el, catsBox, foot);
  const open = () => go({ name: 'drive', letter: L, tab: 'cats' });
  el.addEventListener('click', e => { if (e.target.closest('.scanbtn, .dc-foot button, .sw')) return; open(); });
  el.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target === el) open(); });
  scanB.addEventListener('click', e => { e.stopPropagation(); if (isScanning(L)) cancelScan(L); else startScan([L]); });
  let footSig = '', icoSig = '', catSig = '';
  function update() {
    const d = driveOf(L);
    if (!d) return;
    const conn = isConn(d);
    const scanning = isScanning(L);
    const k = driveIcon(d) + (conn ? '' : '-off');
    if (icoSig !== k) { icoSig = k; icoBox.textContent = ''; icoBox.append(tile('drive', driveIcon(d))); if (!conn) icoBox.append(h('span', { class: 'off-b', html: ic('unplug') })); }
    setH(nm, `${esc(dlabel(d))}${dtag(d) ? `<em>(${esc(dtag(d))})</em>` : ''}`);
    nm._full = dname(d) + (d.unc ? '\n' + d.unc : '');
    setT(kchip, kindLabel(d));
    kchip.hidden = !kchip.textContent;
    nbchip.hidden = hasBin(d) || !conn;
    el.classList.toggle('off', !conn);
    if (conn) { tw(free, d.free, fmtBytes); freeL.textContent = '남음'; }
    else { free._tvSet = false; free.textContent = d.total ? fmtBytes(d.free) : '—'; freeL.textContent = '남음(마지막 기록)'; }
    freeBox.classList.toggle('low', conn && d.total > 0 && d.free / d.total < 0.1);
    setT(used, d.total ? `${fmtBytes(d.total)} 중 ${fmtBytes(d.used)} 사용` : '');
    bar.set(driveVals(d), d.total || 1);
    bar.setScanning(scanning);
    el.classList.toggle('scanning', scanning);
    el.classList.toggle('never', conn && !d.scanned && !scanning);
    scanB.hidden = !conn;
    setH(scanB, ic(scanning ? 'stop' : 'scan'));
    scanB.setAttribute('data-tip', scanning ? '스캔 취소' : d.scanned ? '다시 스캔' : '스캔');
    if (catsBox) {
      const cats = (d.cats || []).map((b, i) => [b, i]).filter(x => x[0] > 0).sort((a, b) => b[0] - a[0]).slice(0, 4);
      const cs = cats.map(x => x.join(':')).join(',');
      if (cs !== catSig) {
        catSig = cs;
        catsBox.textContent = '';
        cats.forEach(([b, i]) => {
          const r = h('div', { class: 'dcc', 'data-c': 'c' + i, style: { '--sc': `var(--c${i})` } }, h('i'), h('span', { text: catName(i) }), h('b', { class: 'num', text: fmtBytes(b) }));
          r.addEventListener('pointerenter', () => HL.set('c' + i));
          r.addEventListener('pointerleave', () => HL.set(null));
          catsBox.append(r);
        });
        catsBox.hidden = !cats.length;
      }
    }
    const sc = S.scans[L];
    let sig;
    if (!conn) {
      sig = 'off|' + d.scanned_at + '|' + Math.floor(nowS() / 60);
      if (footSig !== sig) {
        foot.textContent = '';
        foot.append(h('span', { class: 'offtx', html: ic('unplug') + `<span>연결 안 됨${d.scanned_at ? ` · 마지막 스캔 ${esc(relTime(d.scanned_at))}` : ''}</span>`, 'data-tip': d.last_seen ? `마지막으로 본 시각 ${absTime(d.last_seen)}` : null }), h('span', { class: 'sp' }),
          h('button', { class: 'btn sm ghost', type: 'button', html: '<span>기록 지우기</span>', onclick: e => { e.stopPropagation(); confirmForget(driveOf(L)); } }));
      }
    } else if (scanning) {
      const est = Math.max(1, (d.used || 0) - (d.bin_bytes || 0));
      const pct = Math.min(99, Math.floor((sc.bytes || 0) / est * 100));
      sig = 'scan';
      if (footSig !== sig) {
        foot.textContent = '';
        foot.append(h('span', { class: 'live' }, h('i', { class: 'dot-live' }), h('span', { class: 'num st' })));
      }
      const el2 = $('.st', foot);
      const spd = sc.started_at && sc.bytes ? sc.bytes / Math.max(1, nowS() - sc.started_at) : 0;
      el2.textContent = `${sc.phase === 'finalize' ? '정리하는 중' : '스캔 중'} · ${fmtNum(sc.files)}개 · ${fmtBytes(sc.bytes)} · ${pct}%${d.kind === 'network' && spd ? ` · ${fmtBytes(spd)}/s` : ''}`;
    } else if (!d.scanned) {
      sig = 'never|' + !!d.scan;
      if (footSig !== sig) {
        foot.textContent = '';
        const sb = h('button', { class: 'btn sm', type: 'button', html: ic('scan') + '<span>스캔</span>', onclick: e => { e.stopPropagation(); startScan([L]); } });
        if (d.scan) foot.append(h('span', { text: '아직 스캔하지 않았어요' }), h('span', { class: 'sp' }), sb);
        else {
          const sw = Switch(false, v => setScanTarget(L, v), { label: '모두 스캔에 넣기', tip: '「모두 스캔」에 이 장치도 넣어요' });
          foot.append(h('span', { class: 'swl' }, sw.el, h('span', { text: '모두 스캔에 넣기' })), h('span', { class: 'sp' }), sb);
        }
      }
    } else {
      sig = 'ok|' + d.scanned_at + '|' + d.stale + '|' + d.errors + '|' + d.scan + '|' + Math.floor(nowS() / 60);
      if (footSig !== sig) {
        foot.textContent = '';
        foot.append(h('span', { 'data-tip': absTime(d.scanned_at), text: `${relTime(d.scanned_at)} 스캔` }));
        if (d.errors) foot.append(h('span', { class: 'dotsep', text: '·' }), h('span', { class: 'tdim num', 'data-tip': '권한이 없어 열지 못한 폴더예요. 「시스템·확인 불가」로 셉니다.', text: `확인 불가 ${fmtNum(d.errors)}개` }));
        if (!d.scan) foot.append(h('span', { class: 'dotsep', text: '·' }), h('span', { class: 'tdim', 'data-tip': '「모두 스캔」에서 빠져 있어요. 설정 → 드라이브 스캔 대상에서 바꿀 수 있어요.', text: '자동 스캔 제외' }));
        foot.append(h('span', { class: 'sp' }));
        if (d.stale) foot.append(h('span', { class: 'warn', 'data-tip': '스캔한 뒤 사용량이 크게 바뀌었어요.', html: ic('warn') + '<span>다시 스캔 권장</span>' }));
      }
    }
    footSig = sig;
  }
  return { el, update };
}
function AddNasCard(compact) {
  const el = h('button', { class: 'dcard addcard' + (compact ? ' compact' : ''), type: 'button' },
    h('span', { class: 'ac-ic', html: ic('plus') }),
    h('div', { class: 'ac-t' }, h('b', { text: 'NAS 추가' }), h('span', { text: '\\\\ORICO 처럼 장치 이름만 넣으면 공유 폴더를 찾아 드려요' })));
  el.addEventListener('click', () => openNasSheet());
  return el;
}

function HomeScreen() {
  const el = h('div', {});
  const usedBig = h('span', { class: 'big num' });
  const ofT = h('span', { class: 'of num' });
  const freeV = h('div', { class: 'v num' });
  const freeL = h('div', { class: 'l num' });
  const lastScan = h('span', { class: 'dim' });
  const bar = new Bar('b-lg');
  const legend = new Legend({ onPick: k => { if (k === 'bin') confirmEmptyBin(); else if (k.startsWith('c')) go({ name: 'list', drive: '*', view: 'files', cat: Number(k.slice(1)), age: 0, dir: 0, q: '', sort: 'size', order: 'desc' }); } });
  const heroLabel = h('span', { class: 'num hero-l' });
  const hero = h('section', { class: 'hero' },
    h('div', { class: 'hero-top' }, heroLabel, h('span', { class: 'sp' }), lastScan),
    h('div', { class: 'hero-nums' },
      h('div', { class: 'hero-used' }, ofT, usedBig, h('span', { class: 'u', text: '사용' })),
      h('div', { class: 'hero-free' }, freeV, freeL)),
    bar.el, legend.el);
  el.append(hero);
  const recsH = h('div', { class: 'sec-h' }, h('h2', { text: '정리 추천' }), h('span', { class: 'sub', text: '지우거나 옮기면 좋은 항목' }), h('span', { class: 'sp' }));
  const recsMore = h('button', { class: 'link', type: 'button' });
  recsH.append(recsMore);
  const recsBox = h('div', { class: 'recs' });
  const recsSec = h('section', {}, recsH, recsBox);
  el.append(recsSec);
  const groupsBox = h('div', {});
  el.append(groupsBox);
  const secs = new Map();      // gid -> {sec, h2, sub, grid}
  const cards = new Map();
  let single = null, singleL = null, layout = '';
  let showAllRecs = false;
  let recVer = -1;
  function renderRecs() {
    recVer = S.recsVer;
    recsBox.textContent = '';
    const recs = S.recs || [];
    recsSec.hidden = !recs.length;
    const cols = Math.max(1, Math.floor((recsBox.clientWidth + 12) / (236 + 12)) || 4);
    recs.forEach((r, i) => { const c = recCard(r); if (!showAllRecs && i >= cols) c.classList.add('more-hidden'); recsBox.append(c); });
    const extra = recs.length - cols;
    recsMore.hidden = extra <= 0;
    recsMore.innerHTML = showAllRecs ? '접기' : `${extra}개 더 보기` + ic('chevD');
  }
  recsMore.addEventListener('click', () => { showAllRecs = !showAllRecs; renderRecs(); });
  let lastW = 0;
  if (window.ResizeObserver) {
    new ResizeObserver(() => {
      const w = recsBox.clientWidth;
      if (w && Math.abs(w - lastW) > 4 && S.recs && S.recs.length) { lastW = w; const vs = S.recPrev; renderRecs(); S.recPrev = vs; }
    }).observe(recsBox);
  }
  const GTITLE = { int: '내장 드라이브', ext: '외장·USB', net: '네트워크(NAS)' };
  function sec(gid) {
    let x = secs.get(gid);
    if (x) return x;
    const h2 = h('h2', { text: GTITLE[gid] });
    const sub = h('span', { class: 'sub num' });
    const right = h('span', { class: 'sp' });
    const hd = h('div', { class: 'sec-h' }, h2, sub, right);
    if (gid === 'net') hd.append(h('button', { class: 'link', type: 'button', html: ic('plus') + 'NAS 추가', onclick: () => openNasSheet() }));
    const grid = h('div', { class: 'dgrid' });
    const s = h('section', { class: 'dsec', 'data-g': gid }, hd, grid);
    x = { sec: s, h2, sub, grid, hd };
    secs.set(gid, x);
    return x;
  }
  function clearLayout() {
    groupsBox.textContent = '';
    secs.clear(); cards.clear();
    single = null; singleL = null;
  }
  function update() {
    const t = totals();
    const live = liveDrives();
    const cnt = { int: 0, ext: 0, net: 0 };
    live.forEach(d => { cnt[driveGroup(d)]++; });
    const lab = [cnt.int ? `내장 ${cnt.int}개` : '', cnt.ext ? `외장·USB ${cnt.ext}개` : '', cnt.net ? `NAS ${cnt.net}개` : ''].filter(Boolean).join(' · ');
    heroLabel.textContent = live.length ? (live.length === 1 ? `${dname(live[0])}` : lab) : (S.drives.length ? '연결된 드라이브가 없어요' : '드라이브를 찾는 중');
    tw(usedBig, t.used, fmtBytes, 900);
    ofT.textContent = `${fmtBytes(t.total)} 중`;
    tw(freeV, t.free, fmtBytes, 900);
    freeL.textContent = '남음';
    lastScan.textContent = t.lastScan ? `마지막 스캔 ${relTime(t.lastScan)}` : '';
    lastScan.setAttribute('data-tip', t.lastScan ? absTime(t.lastScan) : '');
    bar.set(t.vals, t.total);
    bar.setScanning(live.some(d => isScanning(d.letter) && !d.scanned));
    legend.set(t.vals, t.free);
    // 배치: 연결된 장치 1개 → 카테고리 목록을 바로 펼친다 / 2개 → 큰 카드 / 그 밖 → 묶음별 그리드
    const all = S.drives;
    const want = live.length === 1 && all.length === 1 ? 'single' : live.length <= 2 && all.length <= 2 ? 'pair' : 'grid';
    if (want !== layout || (want === 'single' && singleL !== live[0].letter)) { clearLayout(); layout = want; }
    if (want === 'single') {
      const d = live[0];
      if (!single) {
        singleL = d.letter;
        const s = sec('int');
        s.h2.textContent = dname(d);
        s.hd.append(h('button', { class: 'link', type: 'button', html: '폴더·트리맵 보기' + ic('chevR'), onclick: () => go({ name: 'drive', letter: d.letter, tab: 'folders' }) }));
        const cl = CategoryList(d.letter), ti = TopItems(d.letter);
        s.grid.className = 'dv-cols';
        s.grid.append(cl.el, ti.el);
        single = { cl, ti, loaded: false };
        groupsBox.append(s.sec, nasInvite());
      }
      secs.get('int').sub.textContent = `${fmtBytes(d.free)} 남음`;
      single.cl.update();
      if (d.scanned && !single.loaded) { single.loaded = true; single.ti.load(); }
    } else {
      const order = [];
      for (const g of GROUPS) {
        const ds = all.filter(d => driveGroup(d) === g.id);
        if (!ds.length && !(g.id === 'net' && want === 'grid')) continue;
        const s = sec(g.id);
        const lv = ds.filter(isConn);
        s.sub.textContent = ds.length ? `${ds.length}개${lv.length ? ' · ' + fmtBytes(sum(lv.map(d => d.total))) : ''}` : '';
        s.grid.classList.toggle('pair', want === 'pair');
        for (const d of ds) {
          let c = cards.get(d.letter);
          if (!c) { c = DriveCard(d, { big: want === 'pair' }); cards.set(d.letter, c); }
          if (c.el.parentNode !== s.grid) s.grid.append(c.el);
          c.update();
        }
        if (g.id === 'net' && !ds.length) {
          if (!s.add) { s.add = AddNasCard(); }
          s.grid.append(s.add);
        } else if (s.add) { s.add.remove(); s.add = null; }
        order.push(s.sec);
      }
      for (const [L, c] of cards) if (!driveOf(L)) { c.el.remove(); cards.delete(L); }
      for (const [gid, s] of secs) if (!order.includes(s.sec)) { s.sec.remove(); secs.delete(gid); }
      if (want === 'pair') order.push(groupsBox._invite || (groupsBox._invite = nasInvite()));
      order.forEach((n, i) => { if (groupsBox.children[i] !== n) groupsBox.insertBefore(n, groupsBox.children[i] || null); });
    }
    if (recVer !== S.recsVer) renderRecs();
    if (!t.scanned && !live.some(d => isScanning(d.letter)) && live.length) {
      if (!el.querySelector('.first-run')) {
        const tgt = live.filter(d => d.scan);
        recsSec.after(h('div', { class: 'card first-run', style: { marginTop: '20px' } },
          h('div', { class: 'empty' }, h('div', { class: 'ill', html: ic('scan') }), h('h3', { text: '아직 스캔하지 않았어요' }),
            h('p', { text: '스캔하면 드라이브마다 무엇이 공간을 차지하는지 카테고리별로 보여 드려요. 스캔 중에도 앱을 쓸 수 있어요.' }),
            h('button', { class: 'btn primary', type: 'button', html: ic('scan') + `<span>${tgt.length === live.length ? '모든 드라이브 스캔' : `드라이브 ${tgt.length}개 스캔`}</span>`, onclick: () => startScan('all') }),
            tgt.length < live.length ? h('p', { class: 'fr-note', text: '외장·USB·NAS 는 기본으로 빼 두었어요. 카드에서 따로 스캔할 수 있어요.' }) : null)));
      }
    } else { const f = el.querySelector('.first-run'); if (f) f.remove(); }
  }
  return { el, update, onRecs: () => { renderRecs(); Rail.update(); }, onState: update, refresh() { if (single) { single.ti.load(); single.cl.recount(); } } };
}
/* 드라이브가 적은 PC: 외장·NAS 로 보관하는 길을 알려 준다(비어 보이지 않게) */
function nasInvite() {
  const el = h('div', { class: 'invite' },
    h('div', { class: 'iv-ics' }, h('span', { class: 'tile', 'data-c': 'drive', html: ic('ext') }), h('span', { class: 'tile', 'data-c': 'drive', html: ic('nas') })),
    h('div', { class: 'iv-t' }, h('b', { text: '외장 디스크·NAS 로 보관하기' }), h('span', { text: '끝난 프로젝트를 옮기거나 복사해 두면 이 PC 의 공간이 늘어나요. 외장 디스크는 꽂으면 바로 보이고, NAS 는 한 번만 추가하면 돼요.' })),
    h('span', { class: 'sp' }),
    h('button', { class: 'btn', type: 'button', html: ic('plus') + '<span>NAS 추가</span>', onclick: () => openNasSheet() }));
  return el;
}

/* ═════════════════════════════════════════════ 드라이브 상세 */
function CategoryList(L) {
  const el = h('div', { class: 'card' });
  const rows = new Map();
  const counts = {};
  let fetched = false;
  function fetchCounts() {
    const d = driveOf(L);
    if (fetched || !d || !d.scanned) return;
    fetched = true;
    (async () => {
      for (let i = 0; i < NCAT; i++) {
        if (!((d.cats || [])[i] > 0)) continue;
        try {
          const r = await GET('/api/items', { drive: L, view: 'files', cat: i, limit: 1, offset: 0, sort: 'size', order: 'desc', dir: 0, age: 0, q: '' });
          counts[i] = (r.total || 0) + ((r.remainder && r.remainder.files) || 0);
          const x = rows.get('c' + i);
          if (x) x.c.textContent = `${fmtNum(counts[i])}개`;
        } catch (e) { /* 개수는 생략 */ }
        if (!el.isConnected) return;
      }
    })();
  }
  function rowFor(k) {
    const isCat = k.startsWith('c');
    const i = isCat ? Number(k.slice(1)) : -1;
    const v = h('div', { class: 'v num' });
    const c = h('div', { class: 'c num' });
    const meter = h('div', { class: 'meter' }, h('i', { style: { '--sc': k === 'bin' ? 'var(--cbin)' : k === 'un' || k === 'ns' ? 'var(--cun)' : `var(--${k})` } }));
    const desc = isCat ? (catOf(i).desc || '') : k === 'bin' ? '휴지통으로 보낸 항목 · 비워야 공간이 생겨요' : k === 'ns' ? '아직 스캔하지 않았어요 · 스캔하면 무엇이 차지하는지 보여 드려요' : '시스템 파일·권한 없는 폴더 등 스캔으로 확인하지 못한 공간';
    const el2 = h(isCat || k === 'bin' ? 'button' : 'div', { class: 'crow' + (isCat ? '' : k === 'bin' ? '' : ' static'), type: 'button', 'data-c': k },
      tile(k, k === 'bin' ? 'trash' : k === 'ns' ? 'scan' : k),
      h('div', { style: { minWidth: '0' } }, h('div', { class: 'n', text: segName(k) }), h('div', { class: 'd', text: desc }), meter),
      h('div', { class: 'r' }, v, c),
      isCat ? icEl('chevR', 'chev') : k === 'bin' ? h('span', { class: 'tdim', style: { fontSize: '12px' }, text: '' }) : h('span'));
    el2.addEventListener('pointerenter', () => HL.set(k));
    el2.addEventListener('pointerleave', () => HL.set(null));
    if (isCat) el2.addEventListener('click', () => go({ name: 'list', drive: L, view: 'files', cat: i, age: 0, dir: 0, q: '', sort: 'size', order: 'desc' }));
    if (k === 'bin') el2.addEventListener('click', () => confirmEmptyBin([L]));
    return { el: el2, v, c, meter: meter.firstChild };
  }
  function update() {
    const d = driveOf(L);
    if (!d) return;
    const vals = driveVals(d);
    const keys = SEGK.filter(k => (vals[k] || 0) > 0);
    const catKeys = keys.filter(k => k.startsWith('c')).sort((a, b) => vals[b] - vals[a]);
    const order = [...catKeys, ...keys.filter(k => !k.startsWith('c'))];
    const max = Math.max(1, ...order.map(k => vals[k]));
    const sig = order.join(',');
    if (el._sig !== sig) {
      el._sig = sig;
      el.textContent = '';
      for (const k of order) { let x = rows.get(k); if (!x) { x = rowFor(k); rows.set(k, x); } el.append(x.el); }
    }
    for (const k of order) {
      const x = rows.get(k);
      tw(x.v, vals[k], fmtBytes);
      if (k.startsWith('c')) { const i = Number(k.slice(1)); if (counts[i] != null) x.c.textContent = `${fmtNum(counts[i])}개`; else x.c.textContent = fmtPct(vals[k], d.total); }
      else if (k === 'bin') x.c.textContent = d.bin_count ? `${fmtNum(d.bin_count)}개 · 비우기` : '비우기';
      else x.c.textContent = fmtPct(vals[k], d.total);
      raf2(() => { x.meter.style.width = (vals[k] / max * 100).toFixed(2) + '%'; });
    }
    fetchCounts();
  }
  function recount() { fetched = false; fetchCounts(); }
  return { el, update, recount };
}

function TopItems(L) {
  const list = h('div', { class: 'mini-rows' });
  const more = h('button', { class: 'link', type: 'button', html: '모두 보기' + ic('chevR') });
  const el = h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h3', { text: '가장 큰 항목' }), h('span', { class: 'sp' }), more), list);
  more.addEventListener('click', () => go({ name: 'list', drive: L, view: 'files', cat: null, age: 0, dir: 0, q: '', sort: 'size', order: 'desc' }));
  let loading = false, loadedOnce = false;
  async function load() {
    const d = driveOf(L);
    if (!d || !d.scanned || loading) return;
    loading = true;
    const animate = !loadedOnce;
    loadedOnce = true;
    try {
      const r = await GET('/api/items', { drive: L, view: 'files', cat: '', dir: 0, age: 0, q: '', sort: 'size', order: 'desc', offset: 0, limit: 8 });
      list.textContent = '';
      (r.items || []).forEach((it, i) => {
        const row = h('button', { class: 'mrow', type: 'button' }, itemTile(it, 't-sm'),
          h('div', { style: { minWidth: '0' } }, h('div', { class: 'n', text: it.name }), h('div', { class: 'p' }, pathEl(parentOf(it.path), 46))),
          h('span', { class: 'v num', text: fmtBytes(it.size) }));
        if (!reduced && animate) { row.style.animation = `row-in .4s var(--ease-out) ${i * 30}ms both`; }
        row.addEventListener('click', () => go({ name: 'list', drive: L, view: 'files', cat: it.cat, age: 0, dir: 0, q: '', sort: 'size', order: 'desc' }));
        row.addEventListener('contextmenu', e => { e.preventDefault(); rowMenu(it, e.clientX, e.clientY); });
        list.append(row);
      });
      list.append(h('div', { class: 'foot' }));
    } catch (e) { list.textContent = ''; list.append(h('div', { class: 'empty', style: { padding: '24px' } }, h('p', { text: e.message }))); }
    loading = false;
  }
  for (let i = 0; i < 6; i++) list.append(h('div', { class: 'skel', style: { gridTemplateColumns: '28px 1fr 60px', height: '46px', padding: '0 16px' } }, h('i', { class: 'sq', style: { width: '28px', height: '28px' } }), h('div', { class: 'two' }, h('i', { style: { width: '70%' } }), h('i', { style: { width: '45%', height: '8px' } })), h('i')));
  return { el, load };
}
const parentOf = p => { const s = String(p || '').replace(/[\\/]+$/, ''); const i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/')); return i > 2 ? s.slice(0, i) : s.slice(0, 3); };
/* 드라이브 루트 아래 경로만(중복 파일 행처럼 드라이브 꼬리표를 따로 붙일 때) */
function relPathEl(p) {
  const { sep, segs } = splitPath(p);
  const rel = segs.join(sep);
  const s = rel ? fmtPath(sep + rel, 90).replace(/^[\\/]/, '') : '';
  const el = h('span', { class: 'pth rel' }, h('span', { class: 'pt' }, h('bdi', { text: s || '(최상위)' })));
  if (s !== rel) el.setAttribute('data-tip', p);
  return el;
}

function scanBox(L) {
  const pct = h('span', { class: 'pct num' });
  const fill = h('i');
  const files = h('span', { class: 'num' }), dirs = h('span', { class: 'num' }), bytes = h('span', { class: 'num' });
  const cur = h('div', { class: 'cur' }, h('bdi'));
  const stopB = h('button', { class: 'btn sm', type: 'button', html: ic('stop') + '<span>취소</span>', onclick: () => cancelScan(L) });
  const el = h('div', { class: 'card scanbox' },
    h('div', { class: 'row1' }, h('span', { class: 'dot-live' }), h('h3', { text: '스캔하는 중' }), pct, h('span', { class: 'sp' }), stopB),
    h('div', { class: 'prog' }, fill),
    h('div', { class: 'stats' }, h('span', {}, '파일 ', h('b', {}, files), '개'), h('span', {}, '폴더 ', h('b', {}, dirs), '개'), h('span', {}, h('b', {}, bytes), ' 확인')),
    cur);
  function update() {
    const d = driveOf(L), sc = S.scans[L];
    if (!d || !sc) return;
    const est = Math.max(1, (d.used || 0) - (d.bin_bytes || 0));
    const p = sc.phase === 'finalize' ? 0.99 : Math.min(0.99, (sc.bytes || 0) / est);
    fill.style.width = (p * 100).toFixed(1) + '%';
    pct.textContent = sc.phase === 'finalize' ? '정리하는 중' : `약 ${Math.floor(p * 100)}%`;
    tw(files, sc.files, fmtNum, 380); tw(dirs, sc.dirs, fmtNum, 380); tw(bytes, sc.bytes, fmtBytes, 380);
    cur.firstChild.textContent = sc.current ? fmtPath(sc.current, 90) : '';
    $('h3', el).textContent = d.scanned ? '다시 스캔하는 중 — 이전 결과를 보여 드려요' : '스캔하는 중';
  }
  return { el, update };
}

function DriveScreen(route) {
  const L = route.letter;
  const el = h('div', {});
  const h1 = h('h1');
  const kchip = h('span', { class: 'kchip' });
  const acts = h('div', { class: 'acts' });
  const sub = h('div', { class: 'dv-sub' });
  const bar = new Bar('b-lg');
  const legend = new Legend({ onPick: k => { if (k === 'bin') confirmEmptyBin([L]); else if (k.startsWith('c')) go({ name: 'list', drive: L, view: 'files', cat: Number(k.slice(1)), age: 0, dir: 0, q: '', sort: 'size', order: 'desc' }); } });
  const d0 = driveOf(L);
  const headIc = h('span', { class: 'dc-ic' }, tile('drive', driveIcon(d0), 't-xl'));
  const offBar = h('div', { class: 'dv-off', hidden: true });
  const dvHead = h('div', { class: 'dv-head' }, headIc, h('div', { style: { minWidth: '0' } }, h('div', { style: { display: 'flex', alignItems: 'center', gap: '10px' } }, h1, kchip), sub), h('span', { class: 'sp' }), acts);
  el.append(dvHead, offBar);
  el.append(h('div', { class: 'dv-bar' }, bar.el), legend.el);
  let tab = route.tab || 'cats';
  const tabs = Tabs([{ id: 'cats', label: '카테고리' }, { id: 'folders', label: '폴더' }, { id: 'treemap', label: '트리맵' }], tab, id => { tab = id; route.tab = id; renderBody(); Top.update(); });
  const tabRight = h('div', { class: 'tabright' });
  el.append(h('div', { class: 'tabrow' }, tabs.el, h('span', { class: 'sp' }), tabRight));
  const body = h('div', {});
  el.append(body);
  let part = null, mode = '';
  function bodyMode() {
    const d = driveOf(L);
    if (!d) return 'none';
    if (!d.scanned) return !isConn(d) ? 'offline' : isScanning(L) ? 'scanning' : 'never';
    return tab;
  }
  function renderBody() {
    if (part && part.destroy) part.destroy();
    part = null;
    body.textContent = '';
    regClear();
    Sel.clear();
    mode = bodyMode();
    const d = driveOf(L);
    tabRight.textContent = '';
    if (mode === 'offline') {
      body.append(h('div', { class: 'card' }, h('div', { class: 'empty' }, h('div', { class: 'ill', html: ic('unplug') }), h('h3', { text: '연결되지 않은 장치예요' }),
        h('p', { text: '저장해 둔 스캔 기록이 없어서 보여 드릴 내용이 없어요. 장치를 다시 연결하면 스캔할 수 있어요.' }))));
      return;
    }
    if (mode === 'never') {
      const net = d.kind === 'network';
      const msg = net ? '스캔하면 카테고리별 용량을 보여 드려요. 네트워크라 시간이 걸릴 수 있어요 — 진행 중 속도를 보여 드리고, 언제든 취소할 수 있어요.'
        : d.kind === 'hdd' ? '스캔하면 카테고리별 용량을 보여 드려요. HDD 는 몇 분 걸릴 수 있어요.' : '스캔하면 카테고리별 용량을 보여 드려요.';
      const box = h('div', { class: 'empty' }, h('div', { class: 'ill', html: ic(driveIcon(d)) }), h('h3', { text: '아직 스캔하지 않았어요' }), h('p', { text: msg }),
        h('button', { class: 'btn primary', type: 'button', html: ic('scan') + '<span>스캔 시작</span>', onclick: () => startScan([L]) }));
      if (!d.scan) {
        const sw = Switch(false, v => setScanTarget(L, v), { label: '모두 스캔에 넣기' });
        box.append(h('div', { class: 'swl big' }, sw.el, h('span', { text: '「모두 스캔」에도 넣기' })));
      }
      body.append(h('div', { class: 'card' }, box));
      return;
    }
    if (mode === 'scanning') { part = scanBox(L); body.append(part.el); part.update(); return; }
    if (isScanning(L)) { const sb = scanBox(L); body.append(sb.el, h('div', { style: { height: '14px' } })); part = { scan: sb, update() { sb.update(); } }; }
    if (mode === 'cats') {
      const cl = CategoryList(L), ti = TopItems(L);
      body.append(h('div', { class: 'dv-cols' }, cl.el, ti.el));
      const prev = part;
      part = { update() { cl.update(); if (prev) prev.update(); }, cl, ti };
      cl.update();
      ti.load();
    } else if (mode === 'folders') {
      const lv = ListView({ drive: L, view: 'folders', cat: null, dir: 0, age: 0, q: '', sort: 'size', order: 'desc', lockView: true, embedded: true });
      body.append(lv.el);
      const prev = part;
      part = { update() { if (prev) prev.update(); }, lv, destroy() { lv.destroy(); } };
      lv.load();
    } else if (mode === 'treemap') {
      const tm = TreemapView(L);
      body.append(tm.el);
      tabRight.append(tm.tools);
      const prev = part;
      part = { update() { if (prev) prev.update(); }, tmv: tm, destroy() { tm.destroy(); } };
    }
  }
  let lastSig = '';
  function update() {
    const d = driveOf(L);
    if (!d) return;
    const tg = dtag(d);
    setH(h1, `${esc(dlabel(d))}${tg ? `<em>(${esc(tg)})</em>` : ''}`);
    setT(kchip, kindLabel(d));
    kchip.hidden = !kchip.textContent;
    const conn = isConn(d);
    el.classList.toggle('dv-offline', !conn);
    const scanning = isScanning(L);
    const sig = [scanning, d.scanned, d.stale, d.scanned_at, d.bin_bytes > 0, conn, d.scan, (d.also_as || []).join(','), Math.floor(nowS() / 60)].join('|');
    if (sig !== lastSig) {
      lastSig = sig;
      headIc.textContent = '';
      headIc.append(tile('drive', driveIcon(d), 't-xl'));
      if (!conn) headIc.append(h('span', { class: 'off-b', html: ic('unplug') }));
      sub.textContent = '';
      if (d.total) sub.append(h('span', { html: `<b class="num">${fmtBytes(d.total)}</b> 중 <b class="num">${fmtBytes(d.used)}</b> 사용 · <b class="num">${fmtBytes(d.free)}</b> 남음${conn ? '' : ' (마지막 기록)'}` }));
      if (d.scanned_at) sub.append(h('span', { class: 'dim', 'data-tip': absTime(d.scanned_at), text: `${relTime(d.scanned_at)} 스캔${d.scan_seconds ? ` · ${fmtDur(d.scan_seconds)} 걸림` : ''}` }));
      if (d.unc) sub.append(h('span', { class: 'dim', 'data-tip': d.unc }, pathEl(d.unc, 40)));
      if (d.stale) sub.append(h('span', { class: 'stale', html: ic('warn') + '<span>스캔 후 사용량이 바뀌었어요</span>' }));
      if (!hasBin(d) && conn) sub.append(h('span', { class: 'kchip nb', 'data-tip': '지우면 바로 영구 삭제돼요.', text: '휴지통 없음' }));
      const twins = d.also_as || [];
      if (twins.length) {
        const lt = twins.filter(isLetterKey);
        sub.append(h('span', { class: 'dim', 'data-tip': '같은 공유가 드라이브 문자와 「NAS 추가」 위치로 두 번 잡혀 있어요.\n한 번만 세도록 이 화면에 함께 보여요.',
          text: lt.length ? `${lt.map(x => x + ':').join(', ')} 와 같은 공유` : 'NAS 위치로도 추가한 공유' }));
      }
      acts.textContent = '';
      offBar.hidden = conn;
      if (!conn) {
        offBar.textContent = '';
        offBar.append(icEl('unplug'), h('div', {}, h('b', { text: '연결되지 않은 장치예요. ' }), d.scanned ? '마지막 스캔 결과를 보여 드려요. 옮기기·지우기는 다시 연결한 뒤에 할 수 있어요.' : '다시 연결하면 스캔할 수 있어요.',
          d.last_letter && d.last_letter !== L ? h('span', { class: 'dim', text: ` 마지막 드라이브 문자 ${d.last_letter}:` }) : null));
        acts.append(h('button', { class: 'btn', type: 'button', html: ic('trash') + '<span>기록 지우기</span>', onclick: () => confirmForget(driveOf(L)) }));
      } else {
        if ((d.bin_bytes || 0) > 0) acts.append(h('button', { class: 'btn', type: 'button', html: ic('trash') + `<span class="num">휴지통 비우기 · ${fmtBytes(d.bin_bytes)}</span>`, onclick: () => confirmEmptyBin([L]) }));
        acts.append(scanning ? h('button', { class: 'btn', type: 'button', html: ic('stop') + '<span>스캔 취소</span>', onclick: () => cancelScan(L) })
          : h('button', { class: 'btn', type: 'button', html: ic('scan') + `<span>${d.scanned ? '다시 스캔' : '스캔'}</span>`, onclick: () => startScan([L]) }));
      }
      const more = h('button', { class: 'ibtn', type: 'button', 'data-tip': '더 보기', html: ic('more') });
      more.addEventListener('click', () => driveMenu(more, driveOf(L)));
      acts.append(more);
    } else {
      const b = sub.querySelector('span');
      if (b) setH(b, `<b class="num">${fmtBytes(d.total)}</b> 중 <b class="num">${fmtBytes(d.used)}</b> 사용 · <b class="num">${fmtBytes(d.free)}</b> 남음`);
    }
    bar.set(driveVals(d), d.total);
    bar.setScanning(scanning);
    legend.set(driveVals(d), d.free);
    const m = bodyMode();
    const wantScanStrip = isScanning(L) && d.scanned;
    const haveScanStrip = !!(part && part.scan);
    if (m !== mode || (m !== 'scanning' && m !== 'never' && wantScanStrip !== haveScanStrip)) renderBody();
    else if (part && part.update) part.update();
  }
  renderBody();
  return {
    el, update,
    get lv() { return part && part.lv; },
    canUp() { return !!(part && ((part.lv && part.lv.canUp()) || (part.tmv && part.tmv.canUp()))); },
    up() { if (part && part.lv) part.lv.up(); else if (part && part.tmv) part.tmv.up(); },
    removeKeys(keys, how) { if (part && part.lv) part.lv.removeKeys(keys, how); },
    refresh(why) {
      if (part && part.lv) part.lv.refresh();
      if (part && part.ti) part.ti.load();
      if (part && part.cl) part.cl.recount();
    },
    destroy() { if (part && part.destroy) part.destroy(); },
    selectAll() { if (part && part.lv) part.lv.selectAll(); },
  };
}

/* 트리맵 탭 */
function TreemapView(L) {
  const crumbs = h('div', { class: 'fcrumbs tm-crumbs' });
  const wrap = h('div', { class: 'tm-wrap' });
  const el = h('div', {}, crumbs, wrap);
  const tools = h('div', { class: 'tdim', style: { fontSize: '12px' }, text: '' });
  let tm = null, depth = 0;
  const TM = window.Treemap;
  const hlFn = k => { if (tm && tm.setHighlight) { try { tm.setHighlight(k && /^c\d+$/.test(k) ? Number(k.slice(1)) : null); } catch (e) { /* */ } } };
  const onResize = () => { if (tm && tm.resize) { try { tm.resize(); } catch (e) { /* */ } } };
  if (!TM || typeof TM.create !== 'function') {
    wrap.append(h('div', { class: 'tm-ph' }, h('div', { class: 'empty' }, h('div', { class: 'ill', html: ic('c9') }), h('h3', { text: '트리맵을 준비하는 중이에요' }),
      h('p', { text: '트리맵 모듈을 아직 불러오지 못했어요. 카테고리·폴더 탭에서 같은 내용을 볼 수 있어요.' }),
      h('button', { class: 'btn', type: 'button', html: ic('list') + '<span>폴더 탭으로</span>', onclick: () => { const b = $$('.tabs button').find(x => x.textContent === '폴더'); if (b) b.click(); } }))));
  } else {
    tools.textContent = '눌러서 들어가기 · Backspace 로 위로';
    raf2(() => {
      try {
        tm = TM.create(wrap, {
          load: dirId => GET('/api/tree', { drive: L, dir: dirId == null ? 0 : dirId, depth: 2 }).then(r => r.node),
          catName: cat => catName(cat == null || cat < 0 || cat > 10 ? 10 : cat),
          colorOf: cat => getComputedStyle(document.documentElement).getPropertyValue('--c' + (cat == null || cat < 0 || cat > 10 ? 10 : cat)).trim() || getComputedStyle(document.documentElement).getPropertyValue('--c10').trim(),
          formatSize: fmtBytes,
          onOpenItem: item => { if (item && item.path && item.kind !== 'dir') POST('/api/reveal', { path: item.path }).catch(e => Toast.err(e)); },
          onContext: (item, x, y) => { if (item && item.path) rowMenu(treeItem(L, item), x, y); },
          onPath: cr => {
            depth = (cr || []).length;
            Top.update();
            crumbs.textContent = '';
            (cr || []).forEach((c, i) => {
              if (i) crumbs.append(icEl('chevR'));
              const last = i === cr.length - 1;
              const b = h('button', { type: 'button', class: last ? 'cur' : '', text: c.name || '' });
              const id = c.id != null ? c.id : c.dir;
              b.addEventListener('click', () => { if (tm && id != null) tm.open(id); });
              crumbs.append(b);
            });
          },
        });
        tm.open(0);
      } catch (e) {
        console.warn('treemap', e);
        wrap.textContent = '';
        wrap.append(h('div', { class: 'tm-ph' }, h('div', { class: 'empty' }, h('h3', { text: '트리맵을 그리지 못했어요' }), h('p', { text: String(e && e.message || e) }))));
      }
    });
    HL.listeners.add(hlFn);
    window.addEventListener('resize', onResize);
  }
  return {
    el, tools,
    canUp: () => !!tm && depth > 1,
    up() { if (tm && tm.up) { try { tm.up(); } catch (e) { /* */ } } },
    destroy() { HL.listeners.delete(hlFn); window.removeEventListener('resize', onResize); if (tm && tm.destroy) { try { tm.destroy(); } catch (e) { /* */ } } tm = null; },
  };
}
function treeItem(L, n) {
  const kind = n.kind || 'dir';
  return { key: `${L}:${kind}:${n.id}`, drive: L, kind, id: n.id, name: n.name, path: n.path, size: n.size, metric: n.size, cat: n.cat, cat_bytes: n.cat_bytes || null, files: n.files || 0, mtime: n.mtime || 0, protected: !!n.protected, protected_reason: n.protected_reason || '' };
}

/* ═════════════════════════════════════════════ 목록 */
function listTitle(r) {
  if (r.q) return `「${r.q}」 검색`;
  if (r.title) return r.title;
  if (r.cat != null) return catName(r.cat);
  if (r.view === 'folders') return '폴더';
  return '큰 파일';
}
const SORTS = [
  { id: 'size|desc', label: '크기 큰 순' },
  { id: 'mtime|desc', label: '최근 수정 순' },
  { id: 'mtime|asc', label: '오래된 순' },
  { id: 'name|asc', label: '이름 순' },
];
const AGES = [{ id: 0, label: '전체' }, { id: 90, label: '3개월+' }, { id: 180, label: '6개월+' }, { id: 365, label: '1년+' }];

function ListView(o) {
  const lv = {
    o: Object.assign({ drive: '*', view: 'files', cat: null, dir: 0, age: 0, q: '', sort: 'size', order: 'desc' }, o),
    items: [], total: 0, tb: 0, rem: null, crumbs: [], loading: false, done: false, gen: 0, max: 0, rootAll: o.drive === '*',
    onMeta: o.onMeta || null,
  };
  const el = h('div', { class: 'lv' });
  const barEl = h('div', { class: 'lv-bar' });
  const tools = h('div', { class: 'lv-tools' });
  const viewTabs = Tabs([{ id: 'files', label: '파일' }, { id: 'folders', label: '폴더' }], lv.o.view, v => { lv.o.view = v; if (v === 'folders') { lv.o.dir = 0; if (lv.rootAll) lv.o.drive = '*'; } lv.o.q = ''; syncRoute(); lv.load(); }, 'sm');
  if (!o.lockView) tools.append(viewTabs.el);
  const ageTabs = Tabs(AGES.map(a => ({ id: a.id, label: a.label })), lv.o.age, id => { lv.o.age = id; syncRoute(); lv.load(); }, 'sm');
  const ageBox = h('div', { class: 'agebox', 'data-tip': '수정한 지 이 기간이 지난 항목만 보여요' }, icEl('clock'), ageTabs.el);
  tools.append(ageBox, h('span', { class: 'sp' }));
  const summary = h('span', { class: 'tdim num', style: { fontSize: '12.5px' } });
  if (o.embedded) tools.append(summary);
  const sortDD = h('button', { class: 'dd', type: 'button' });
  tools.append(sortDD);
  sortDD.addEventListener('click', () => {
    if (sortDD.classList.contains('open')) { Pop.closeAll(); return; }
    sortDD.classList.add('open');
    const cur = lv.o.sort + '|' + lv.o.order;
    const p = Menu.open(sortDD, SORTS.map(s => ({ label: s.label, check: s.id === cur, onClick: () => { const [a, b] = s.id.split('|'); lv.o.sort = a; lv.o.order = b; syncRoute(); lv.load(); } })), { place: 'bottom-end', onClose: () => sortDD.classList.remove('open') });
    p.el.style.minWidth = '170px';
  });
  const crumbsEl = h('div', { class: 'fcrumbs' });
  barEl.append(crumbsEl);
  barEl.append(tools);
  const catBox = h('div', { class: 'catchips' });
  barEl.append(catBox);
  const ckAll = h('button', { class: 'ck', type: 'button', html: ic('check'), 'data-tip': '불러온 항목 모두 선택 (Ctrl+A)' });
  const colName = h('button', { type: 'button', 'data-s': 'name' });
  const colSize = h('button', { type: 'button', 'data-s': 'size', class: 'r' });
  const colDate = h('button', { type: 'button', 'data-s': 'mtime', class: 'r' });
  const colh = h('div', { class: 'colh' }, ckAll, h('span'), colName, colSize, colDate);
  barEl.append(colh);
  el.append(barEl);
  const rowsEl = h('div', { class: 'rows', role: 'list' });
  const foot = h('div', { class: 'lv-foot' });
  const sentinel = h('div', { style: { height: '1px' } });
  el.append(rowsEl, foot, sentinel);

  ckAll.addEventListener('click', () => {
    const sels = lv.items.filter(selectable);
    const all = sels.length && sels.every(i => S.sel.has(i.key));
    Sel.set(sels, !all);
    syncCkAll();
  });
  [colName, colSize, colDate].forEach(b => b.addEventListener('click', () => {
    const s = b.dataset.s;
    if (lv.o.sort === s) lv.o.order = lv.o.order === 'desc' ? 'asc' : 'desc';
    else { lv.o.sort = s; lv.o.order = s === 'name' ? 'asc' : 'desc'; }
    syncRoute(); lv.load();
  }));

  function syncRoute() {
    if (!o.embedded) {
      Object.assign(S.route, { view: lv.o.view, age: lv.o.age, sort: lv.o.sort, order: lv.o.order, dir: lv.o.dir, drive: lv.o.drive, q: lv.o.q, cat: lv.o.cat });
      delete S.route.title;
    }
    Top.update();
  }
  function syncCkAll() {
    const sels = lv.items.filter(selectable);
    const n = sels.filter(i => S.sel.has(i.key)).length;
    ckAll.disabled = !sels.length;
    ckAll.classList.toggle('on', !!sels.length && n === sels.length);
    ckAll.classList.toggle('part', n > 0 && n < sels.length);
    ckAll.innerHTML = n > 0 && n < sels.length ? ic('minus') : ic('check');
  }
  lv.syncCkAll = syncCkAll;

  function headUpdate() {
    const so = lv.o.sort, or = lv.o.order;
    const arrow = s => (so === s ? ic(or === 'desc' ? 'down' : 'up') : '');
    colName.innerHTML = `<span>이름</span>${arrow('name')}`;
    colSize.innerHTML = `<span>${lv.o.cat != null && lv.o.view === 'folders' ? esc(catName(lv.o.cat)) + ' 크기' : '크기'}</span>${arrow('size')}`;
    colDate.innerHTML = `<span>수정한 지</span>${arrow('mtime')}`;
    [colName, colSize, colDate].forEach(b => b.classList.toggle('on', b.dataset.s === so));
    const cur = SORTS.find(s => s.id === so + '|' + or);
    sortDD.innerHTML = ic('sort') + `<span>${cur ? cur.label : '정렬'}</span>` + ic('chevD', 'cv');
    viewTabs.set(lv.o.view);
    ageTabs.set(lv.o.age);
    // 카테고리 필터 칩(폴더 보기)
    catBox.textContent = '';
    catBox.hidden = !(lv.o.view === 'folders' && !o.lockCat);
    if (!catBox.hidden) {
      const d = lv.o.drive !== '*' ? driveOf(lv.o.drive) : null;
      const cats = d ? (d.cats || []) : totals().vals;
      const mk = (id, label) => {
        const c = h('button', { class: 'chip' + (lv.o.cat === id ? ' on' : ''), type: 'button', 'data-c': id == null ? null : 'c' + id }, id == null ? null : h('i'), label);
        c.addEventListener('click', () => { lv.o.cat = id; syncRoute(); lv.load(); });
        if (id != null) { c.addEventListener('pointerenter', () => HL.set('c' + id)); c.addEventListener('pointerleave', () => HL.set(null)); }
        return c;
      };
      catBox.append(mk(null, '모든 카테고리'));
      for (let i = 0; i < NCAT; i++) { const b = d ? cats[i] : cats['c' + i]; if (b > 0) catBox.append(mk(i, catName(i))); }
    }
    // 폴더 경로
    crumbsEl.textContent = '';
    crumbsEl.hidden = lv.o.view !== 'folders';
    if (!crumbsEl.hidden) {
      crumbsEl.append(icEl('folder'));
      const cr = [];
      if (lv.rootAll) cr.push({ name: '모든 드라이브', all: true });
      (lv.crumbs || []).forEach(c => cr.push(c));
      cr.forEach((c, i) => {
        if (i) crumbsEl.append(icEl('chevR', 'sep'));
        const last = i === cr.length - 1;
        const b = h('button', { type: 'button', class: last ? 'cur' : '', text: c.name });
        b.addEventListener('click', () => { if (c.all) { lv.o.drive = '*'; lv.o.dir = 0; } else lv.o.dir = c.dir; syncRoute(); lv.load(true, 'fwd'); });
        crumbsEl.append(b);
      });
    }
    let s = `${fmtNum(lv.total)}개 · ${fmtBytes(lv.tb)}`;
    summary.textContent = lv.items.length || lv.total ? s : '';
    if (lv.onMeta) lv.onMeta(lv);
  }

  function rowEl(it, i, anim) {
    if (S.pendKeys.has(it.key)) it._pending = true;
    const sel = S.sel.has(it.key);
    let ck;
    if (it.kind === 'loose') ck = h('span', { class: 'ck none' });
    else if (it.protected) ck = h('span', { class: 'ck lock', 'data-tip': it.protected_reason || '보호된 항목이에요.', html: ic('lock') });
    else ck = h('button', { class: 'ck', type: 'button', 'aria-label': '선택', html: ic('check') });
    const t = h('div', { class: 't' }, h('span', { text: it.name }));
    if (it.kind === 'seq' && it.count) t.append(h('span', { class: 'badge', 'data-tip': it.first != null ? `프레임 ${it.first}–${it.last}` : null, text: `${fmtNum(it.count)}프레임` }));
    let subTxt;
    if (it.kind === 'dir') {
      const cb = it.cat_bytes || [];
      const top = cb.map((b, j) => [b, j]).filter(x => x[0] > 0).sort((a, b) => b[0] - a[0]);
      subTxt = `파일 ${fmtNum(it.files)}개` + (top.length ? ' · ' + top.slice(0, 2).map(([b, j]) => `${catName(j)} ${fmtBytes(b)}`).join(' · ') + (top.length > 2 ? ' 외' : '') : '');
    } else if (it.kind === 'loose') subTxt = `작은 파일 ${fmtNum(it.files)}개 · 큰 파일 기준(${S.settings.big_file_mb || 50}MB) 미만`;
    else subTxt = null;
    const nm = h('div', { class: 'nm' }, t, h('div', { class: 'p' }, subTxt != null ? h('bdi', { text: subTxt }) : pathEl(parentOf(it.path), 96)));
    const v = h('div', { class: 'v num' });
    if (it.kind === 'dir' && lv.o.cat != null && it.metric !== it.size) v.innerHTML = `<small>전체 ${fmtBytes(it.size)}</small>${fmtBytes(it.metric)}`;
    else v.textContent = fmtBytes(it.metric != null ? it.metric : it.size);
    const mb = h('div', { class: 'mb' }, h('i'));
    const szc = h('div', { class: 'szc' }, v, mb);
    const dt = h('div', { class: 'dtc num', text: relTime(it.mtime), 'data-tip': it.mtime ? `수정한 시각 ${absTime(it.mtime)}` : null });
    const row = h('div', { class: 'row' + (sel ? ' sel' : '') + (it.kind === 'loose' ? ' loose' : '') + (it._pending ? ' pending' : '') + (S.copyKeys.has(it.key) ? ' copying' : ''), 'data-key': it.key, role: 'listitem' }, ck, itemTile(it, ''), nm, szc, dt);
    const idv = driveOf(it.drive) || drivePath(it.path);
    if (it.kind !== 'loose' && (!idv || isConn(idv))) {
      const qa = h('div', { class: 'qa' });
      qa.append(h('button', { class: 'ibtn', type: 'button', 'data-a': 'reveal', 'data-tip': '탐색기에서 보기', html: ic('reveal') }));
      if (!it.protected) {
        const nb = !hasBin(driveOf(it.drive) || drivePath(it.path));
        qa.append(h('button', { class: 'ibtn', type: 'button', 'data-a': 'move', 'data-tip': '다른 위치로 이동', html: ic('move') }));
        qa.append(h('button', { class: 'ibtn', type: 'button', 'data-a': 'copy', 'data-tip': '다른 위치로 복사', html: ic('copy') }));
        qa.append(h('button', { class: 'ibtn danger', type: 'button', 'data-a': 'recycle', 'data-tip': nb ? '삭제(복구 불가)' : '휴지통으로', html: ic('trash') }));
      }
      row.append(qa);
    }
    row.append(h('span', { class: 'pstripe' }));
    paintBar(row, it);
    if (anim === 'stagger' && i < 20 && !reduced) { row.classList.add('in'); row.style.animationDelay = (i * 18) + 'ms'; }
    if (anim === 'grow' && !reduced) row.classList.add('grow');
    regRow(it, row);
    row._it = it;
    return row;
  }
  function paintBar(row, it) {
    const i = row.querySelector('.mb > i');
    if (!i) return;
    const m = it.metric != null ? it.metric : it.size;
    const w = lv.max > 0 ? Math.max(m > 0 ? 1.5 : 0, m / lv.max * 100) : 0;
    i.style.width = w.toFixed(2) + '%';
    const ck = it.key + '|' + (it.cat_bytes ? it.cat_bytes.join(',') : it.cat) + '|' + lv.o.cat;
    if (i._k !== ck) {
      i._k = ck;
      i.textContent = '';
      const cb = it.kind === 'dir' && it.cat_bytes ? it.cat_bytes : null;
      if (cb) {
        const tot = sum(cb) || 1;
        cb.forEach((b, j) => { if (b > 0) i.append(h('b', { style: { width: (b / tot * 100).toFixed(2) + '%', '--sc': `var(--c${j})`, opacity: lv.o.cat != null && j !== lv.o.cat ? '.38' : '1' } })); });
      } else i.append(h('b', { style: { width: '100%', '--sc': `var(--c${it.cat == null ? 10 : it.cat})` } }));
    }
  }
  function recomputeMax() {
    const m = Math.max(0, ...lv.items.map(x => (x.kind === 'loose' ? 0 : (x.metric != null ? x.metric : x.size)) || 0));
    if (m !== lv.max) { lv.max = m; for (const x of lv.items) { const r = REG.byKey.get(x.key); if (r && r.el) paintBar(r.el, x); } }
  }
  function renderFoot() {
    foot.textContent = '';
    const rem = lv.rem;
    if (lv.o.view === 'files' && rem && rem.bytes > 0 && lv.done) {
      foot.append(h('div', { class: 'remain' }, icEl('info'), h('span', { html: `목록에 없는 작은 파일(${S.settings.big_file_mb || 50}MB 미만) <b class="num">${fmtNum(rem.files)}개 · ${fmtBytes(rem.bytes)}</b> 는 폴더 합계에만 들어가요. 폴더 보기에서 폴더째로 고를 수 있어요.` })));
    }
    if (!lv.done && lv.items.length) foot.append(h('div', { class: 'loadmore' }, icEl('spinner', 'spin'), h('span', { class: 'num', text: `${fmtNum(lv.items.length)} / ${fmtNum(lv.total)}개 불러옴` })));
  }
  function emptyState() {
    let title = '조건에 맞는 항목이 없어요', p = '기간이나 카테고리 조건을 바꿔 보세요.';
    if (lv.o.q) { title = `「${lv.o.q}」 검색 결과가 없어요`; p = `큰 파일(${S.settings.big_file_mb || 50}MB 이상)의 이름에서 찾아요. 폴더 보기에서는 폴더 이름을 찾아요.`; }
    else if (lv.o.view === 'folders' && !lv.o.age) { title = '빈 폴더예요'; p = '이 폴더에는 보여 드릴 항목이 없어요.'; }
    return h('div', { class: 'empty' }, h('div', { class: 'ill', html: ic(lv.o.q ? 'search' : 'folder') }), h('h3', { text: title }), h('p', { text: p }));
  }
  function skeleton() {
    const f = document.createDocumentFragment();
    for (let i = 0; i < 9; i++) {
      f.append(h('div', { class: 'skel', style: { opacity: String(1 - i * 0.09) } }, h('i', { class: 'ckb' }), h('i', { class: 'sq' }),
        h('div', { class: 'two' }, h('i', { style: { width: (40 + (i * 37) % 35) + '%' } }), h('i', { style: { width: (25 + (i * 53) % 30) + '%', height: '8px' } })),
        h('div', { class: 'two', style: { alignItems: 'flex-end' } }, h('i', { style: { width: '54px' } }), h('i', { style: { width: '100%', height: '3px' } })),
        h('i', { style: { width: '52px', justifySelf: 'end' } })));
    }
    return f;
  }
  function query(offset, limit) {
    return GET('/api/items', { drive: lv.o.drive, view: lv.o.view, cat: lv.o.cat == null ? '' : lv.o.cat, dir: lv.o.dir || 0, age: lv.o.age || 0, q: lv.o.q || '', sort: lv.o.sort, order: lv.o.order, offset, limit });
  }
  lv.load = async (reset = true, how) => {
    const g = ++lv.gen;
    lv.loading = true;
    lv.done = false;
    if (reset) {
      Sel.clear();
      [...REG.byKey.keys()].forEach(k => unregRow(k));
      headUpdate();
    }
    const skT = setTimeout(() => { if (g === lv.gen) { rowsEl.textContent = ''; rowsEl.append(skeleton()); foot.textContent = ''; } }, 140);
    try {
      const r = await query(0, 200);
      clearTimeout(skT);
      if (g !== lv.gen) return;
      lv.items = r.items || [];
      lv.total = r.total || 0;
      lv.tb = r.total_bytes || 0;
      lv.rem = r.remainder || null;
      lv.crumbs = r.crumbs || [];
      lv.done = lv.items.length >= lv.total;
      lv.max = 0;
      rowsEl.textContent = '';
      recomputeMaxSilent();
      const f = document.createDocumentFragment();
      lv.items.forEach((it, i) => f.append(rowEl(it, i, 'stagger')));
      rowsEl.append(f);
      if (!lv.items.length) rowsEl.append(emptyState());
      headUpdate();
      if (how === 'fwd' || how === 'bwd') crumbsEl.classList.add('anim');
      renderFoot();
      syncCkAll();
      if (!o.embedded) $('#view').scrollTop = 0;
    } catch (e) {
      clearTimeout(skT);
      if (g !== lv.gen) return;
      rowsEl.textContent = '';
      rowsEl.append(h('div', { class: 'empty' }, h('div', { class: 'ill', html: ic('warn') }), h('h3', { text: '목록을 불러오지 못했어요' }), h('p', { text: e.message })));
    } finally { if (g === lv.gen) lv.loading = false; }
  };
  function recomputeMaxSilent() { lv.max = Math.max(0, ...lv.items.map(x => (x.kind === 'loose' ? 0 : (x.metric != null ? x.metric : x.size)) || 0)); }
  lv.more = async () => {
    if (lv.loading || lv.done) return;
    const g = lv.gen;
    lv.loading = true;
    try {
      const r = await query(lv.items.length, 200);
      if (g !== lv.gen) return;
      const its = (r.items || []).filter(it => !REG.byKey.has(it.key));
      lv.total = r.total || lv.total;
      lv.items.push(...its);
      lv.done = !its.length || lv.items.length >= lv.total;
      const f = document.createDocumentFragment();
      its.forEach((it, i) => f.append(rowEl(it, i, 'none')));
      rowsEl.append(f);
      recomputeMax();
      renderFoot();
      syncCkAll();
    } catch (e) { Toast.err(e, '더 불러오지 못했어요'); } finally { if (g === lv.gen) lv.loading = false; }
  };
  // 재조정: 같은 조건으로 다시 받아 행을 그 자리에서 고친다(사라진 행은 접고, 새 행은 펼친다)
  lv.refresh = async () => {
    if (lv.loading) return;
    const g = lv.gen;
    const n = Math.max(200, lv.items.length);
    let next = [], total = 0, tb = 0, rem = null;
    try {
      for (let off = 0; off < n; off += 500) {
        const lim = Math.min(500, n - off);
        const r = await query(off, lim);
        if (g !== lv.gen) return;
        const its = r.items || [];
        next.push(...its);
        total = r.total || 0; tb = r.total_bytes || 0; rem = r.remainder || null;
        if (its.length < lim) break;
      }
    } catch (e) { return; }
    if (g !== lv.gen) return;
    const seenK = new Set();
    next = next.filter(it => { if (seenK.has(it.key)) return false; seenK.add(it.key); return true; });
    const ident = it => it.key + '|' + pk(it.path);
    const nk = new Set(next.map(ident));
    for (const it of lv.items) {
      if (!nk.has(ident(it))) { const x = REG.byKey.get(it.key); if (x && x.el && !x.el.classList.contains('gone')) collapse(x.el, ''); unregRow(it.key); S.sel.delete(it.key); }
    }
    lv.items = next;
    lv.total = total;
    lv.tb = tb;
    lv.rem = rem;
    lv.done = next.length >= lv.total;
    recomputeMaxSilent();
    let ref = rowsEl.firstElementChild;
    const skip = () => { while (ref && (ref.classList.contains('gone') || !ref._it)) ref = ref.nextElementSibling; };
    rowsEl.querySelectorAll(':scope > .empty').forEach(e => e.remove());
    next.forEach((it, i) => {
      skip();
      const x = REG.byKey.get(it.key);
      if (x && x.el && ident(x.it) === ident(it)) {
        const pend = S.pendKeys.has(it.key);
        it._pending = pend;
        x.it = it;
        x.el._it = it;
        x.el.classList.toggle('pending', pend);
        const v = x.el.querySelector('.szc .v');
        if (v) {
          if (it.kind === 'dir' && lv.o.cat != null && it.metric !== it.size) setH(v, `<small>전체 ${fmtBytes(it.size)}</small>${fmtBytes(it.metric)}`);
          else setT(v, fmtBytes(it.metric != null ? it.metric : it.size));
        }
        const dt = x.el.querySelector('.dtc');
        if (dt) setT(dt, relTime(it.mtime));
        paintBar(x.el, it);
        if (x.el !== ref) rowsEl.insertBefore(x.el, ref); else ref = ref.nextElementSibling;
        if (S.sel.has(it.key)) S.sel.set(it.key, it);
      } else {
        if (x && x.el) { collapse(x.el, ''); unregRow(it.key); }
        const row = rowEl(it, i, 'grow');
        rowsEl.insertBefore(row, ref);
      }
    });
    if (!next.length) rowsEl.append(emptyState());
    headUpdate();
    renderFoot();
    syncCkAll();
    Tray.update();
  };
  function collapse(row, how) {
    if (how) row.classList.add(how);
    row.classList.add('gone');
    setTimeout(() => row.remove(), reduced ? 0 : 380);
  }
  lv.removeKeys = (keys, how) => {
    const ks = new Set(keys);
    const gone = lv.items.filter(i => ks.has(i.key));
    if (!gone.length) return;
    gone.forEach(it => { const x = REG.byKey.get(it.key); if (x && x.el) collapse(x.el, how); unregRow(it.key); S.sel.delete(it.key); });
    lv.items = lv.items.filter(i => !ks.has(i.key));
    lv.total = Math.max(0, lv.total - gone.length);
    lv.tb = Math.max(0, lv.tb - sum(gone.map(i => i.metric != null ? i.metric : i.size)));
    Tray.update();
    headUpdate();
    syncCkAll();
  };
  lv.selectAll = () => {
    const sels = lv.items.filter(selectable);
    Sel.set(sels, true);
    syncCkAll();
    if (!lv.done && lv.total > lv.items.length) Toast.show({ kind: 'info', title: `불러온 ${fmtNum(sels.length)}개를 골랐어요`, sub: `전체 ${fmtNum(lv.total)}개 중 나머지는 아래로 스크롤하면 더 불러와요.` });
  };
  lv.canUp = () => lv.o.view === 'folders' && ((lv.o.dir && lv.o.dir !== 0) || (lv.rootAll && lv.o.drive !== '*'));
  lv.up = () => {
    if (lv.o.view !== 'folders') return;
    if (lv.o.dir) { const cr = lv.crumbs; lv.o.dir = cr.length >= 2 ? cr[cr.length - 2].dir : 0; }
    else if (lv.rootAll && lv.o.drive !== '*') lv.o.drive = '*';
    syncRoute();
    lv.load(true, 'bwd');
  };
  lv.openDir = it => {
    if (lv.o.drive === '*') { lv.o.drive = it.drive; lv.o.dir = 0; } else lv.o.dir = it.id;
    syncRoute();
    lv.load(true, 'fwd');
  };
  lv.setRoute = r => { Object.assign(lv.o, { q: r.q || '', view: r.view || lv.o.view }); lv.load(); };

  // 이벤트(위임)
  rowsEl.addEventListener('click', e => {
    if (Drag.suppressClick) return;
    const row = e.target.closest('.row');
    if (!row || !row._it) return;
    const it = row._it;
    const a = e.target.closest('[data-a]');
    if (a) {
      e.stopPropagation();
      if (a.dataset.a === 'reveal') reveal(it.path);
      else if (a.dataset.a === 'recycle') confirmRecycle(S.sel.has(it.key) ? Sel.items() : [it]);
      else if (a.dataset.a === 'move' || a.dataset.a === 'copy') MovePop.open(a, S.sel.has(it.key) ? Sel.items() : [it], { place: 'bottom-end', mode: a.dataset.a });
      return;
    }
    if (it.kind === 'loose') return;
    if (e.target.closest('.ck')) { if (e.shiftKey && S.anchor) rangeSel(it); else Sel.toggle(it); syncCkAll(); return; }
    if (it.kind === 'dir' && lv.o.view === 'folders' && !e.shiftKey && !e.ctrlKey && !e.metaKey) { lv.openDir(it); return; }
    if (e.shiftKey && S.anchor) rangeSel(it);
    else Sel.toggle(it);
    syncCkAll();
  });
  rowsEl.addEventListener('dblclick', e => {
    const row = e.target.closest('.row');
    if (!row || !row._it || e.target.closest('.ck, .qa')) return;
    const it = row._it;
    if (it.kind === 'file' || it.kind === 'seq') { reveal(it.path); Sel.toggle(it, S.sel.has(it.key)); }
  });
  rowsEl.addEventListener('contextmenu', e => {
    const row = e.target.closest('.row');
    if (!row || !row._it) return;
    e.preventDefault();
    if (row._it.kind === 'loose') return;
    rowMenu(row._it, e.clientX, e.clientY);
  });
  rowsEl.addEventListener('pointerdown', e => {
    const row = e.target.closest('.row');
    if (!row || !row._it) return;
    if (e.shiftKey) e.preventDefault();
    Drag.arm(e, row._it, row);
  });
  function rangeSel(it) {
    const a = lv.items.findIndex(x => x.key === S.anchor);
    const b = lv.items.findIndex(x => x.key === it.key);
    if (a < 0 || b < 0) { Sel.toggle(it); return; }
    const [x, y] = a < b ? [a, b] : [b, a];
    Sel.set(lv.items.slice(x, y + 1), true);
  }
  let io = null;
  if (window.IntersectionObserver) {
    io = new IntersectionObserver(ents => { if (ents.some(en => en.isIntersecting)) lv.more(); }, { root: $('#view'), rootMargin: '700px 0px' });
    io.observe(sentinel);
  }
  lv.destroy = () => { if (io) io.disconnect(); lv.gen++; };
  lv.el = el;
  headUpdate();
  return lv;
}

function ListScreen(route) {
  const el = h('div', {});
  const tl = h('h1');
  const sub = h('div', { class: 'sub num' });
  const iconBox = h('span');
  el.append(h('div', { class: 'lv-head' }, iconBox, h('div', { style: { minWidth: '0' } }, tl, sub)));
  const lv = ListView({
    drive: route.drive || '*', view: route.view || 'files', cat: route.cat == null ? null : route.cat, dir: route.dir || 0, age: route.age || 0,
    q: route.q || '', sort: route.sort || 'size', order: route.order || 'desc',
    onMeta: x => {
      const r = S.route;
      tl.textContent = listTitle(Object.assign({}, r, { q: x.o.q, cat: x.o.cat, view: x.o.view }));
      const parts = [];
      parts.push(x.o.drive === '*' ? '모든 드라이브' : esc(dname(driveOf(x.o.drive))));
      if (x.items.length || x.total) parts.push(`<b>${fmtNum(x.total)}개</b> · <b>${fmtBytes(x.tb)}</b>`);
      if (x.o.age) parts.push(`${ageText(x.o.age)} 넘게 수정하지 않음`);
      sub.innerHTML = parts.join(' · ');
      iconBox.textContent = '';
      if (x.o.q) iconBox.append(tile('drive', 'search', 't-lg'));
      else if (x.o.cat != null) iconBox.append(tile('c' + x.o.cat, 'c' + x.o.cat, 't-lg'));
      else iconBox.append(tile('drive', x.o.view === 'folders' ? 'folder' : 'big', 't-lg'));
    },
  });
  el.append(lv.el);
  lv.load();
  return {
    el, lv,
    update() {},
    setRoute(r) { lv.setRoute(r); },
    canUp: () => lv.canUp(),
    up: () => lv.up(),
    removeKeys: (k, how) => lv.removeKeys(k, how),
    refresh: () => lv.refresh(),
    selectAll: () => lv.selectAll(),
    destroy: () => lv.destroy(),
  };
}

/* ═════════════════════════════════════════════ 중복 파일 */
function DupesScreen() {
  const el = h('div', {});
  const sub = h('div', { class: 'sub num' });
  const verifyB = h('button', { class: 'btn', type: 'button', html: ic('dupes') + '<span>내용 확인(빠른 해시)</span>' });
  el.append(h('div', { class: 'lv-head' }, tile('c4', 'dupes', 't-lg'), h('div', { style: { minWidth: '0', flex: '1' } }, h('h1', { text: '중복 파일' }), sub), verifyB));
  el.append(h('div', { style: { height: '10px' } }), note('', 'info', '이름과 크기가 같은 파일을 묶었어요. 내용까지 같은지는 「내용 확인」으로 처음·가운데·끝 1MB 를 비교해 확인할 수 있어요. 지울 사본을 골라 휴지통으로 보내세요.'));
  const box = h('div', {});
  el.append(box);
  verifyB.addEventListener('click', async () => {
    try { const r = await POST('/api/dupes/verify', {}); Jobs.track(r.job_id, { kind: 'verify_dupes' }); } catch (e) { Toast.err(e, '확인을 시작하지 못했어요'); }
  });
  let items = [];
  let dgs = [];   // 묶음: { name, verified, keys:Set, its:[] } — 확인창이 「남길 사본이 없는」 묶음을 알아본다
  async function load() {
    box.textContent = '';
    box.append(h('div', { class: 'loadmore' }, icEl('spinner', 'spin'), h('span', { text: '중복 파일을 찾는 중' })));
    try {
      const r = await GET('/api/dupes', { min_mb: S.settings.big_file_mb || 50 });
      const groups = r.groups || [];
      sub.innerHTML = `<b>${fmtNum(groups.length)}묶음</b> · 낭비 <b>${fmtBytes(r.wasted_total)}</b>`;
      box.textContent = '';
      regClear();
      items = [];
      dgs = [];
      if (!groups.length) { box.append(h('div', { class: 'empty' }, h('div', { class: 'ill', html: ic('dupes') }), h('h3', { text: '중복 파일이 없어요' }), h('p', { text: '다른 위치에 있는 같은 이름·같은 크기의 파일을 찾지 못했어요.' }))); return; }
      groups.forEach((g, gi) => {
        const vb = g.verified === true ? h('span', { class: 'vbadge yes', text: '앞·가운데·끝 같음', 'data-tip': '처음·가운데·끝 1MB 와 크기가 같아요.\n파일 전체를 비교한 것은 아니에요 — 마지막 사본을 지우기 전엔 직접 확인해 주세요.' }) : g.verified === false ? h('span', { class: 'vbadge no', text: '내용 다름', 'data-tip': '같은 이름·크기지만 내용이 달라요. 중복이 아니에요.' }) : h('span', { class: 'vbadge unk', text: '미확인' });
        const card = h('div', { class: 'card dg' }, h('div', { class: 'dg-h' }, tile('c4', 'dupes', 't-sm'), h('span', { class: 'n', text: g.name }), h('span', { class: 's num', text: `${fmtBytes(g.size)} × ${g.items.length}` }), vb, h('span', { class: 'sp' }), h('span', { class: 'w num', text: `낭비 ${fmtBytes(g.wasted)}` })));
        const rows = h('div', { class: 'rows', style: { padding: '0 0 6px' } });
        const dg = { name: g.name, verified: g.verified, keys: new Set(), its: [] };
        dgs.push(dg);
        g.items.forEach(x => {
          const it = { key: x.key || `${x.drive}:file:${gi}:${x.path}`, drive: x.drive, kind: 'file', id: null, name: nameOf(x.path), path: x.path, size: g.size, metric: g.size, cat: null, cat_bytes: null, files: 1, mtime: x.mtime, protected: false, protected_reason: '', _dg: dg };
          dg.keys.add(it.key);
          dg.its.push(it);
          items.push(it);
          const row = h('div', { class: 'row' + (S.sel.has(it.key) ? ' sel' : ''), 'data-key': it.key },
            h('button', { class: 'ck', type: 'button', html: ic('check') }),
            tile('drive', driveIcon(driveOf(x.drive) || drivePath(x.path)), 't-sm'),
            h('div', { class: 'nm' }, h('div', { class: 't' }, h('span', { class: 'dtag', text: dtag(driveOf(x.drive)) || dlabel(driveOf(x.drive)) || x.drive }), relPathEl(parentOf(x.path)))),
            h('div', { class: 'szc' }, h('div', { class: 'v num', text: fmtBytes(g.size) })),
            h('div', { class: 'dtc num', text: relTime(x.mtime), 'data-tip': absTime(x.mtime) }),
            h('div', { class: 'qa' }, h('button', { class: 'ibtn', type: 'button', 'data-a': 'reveal', 'data-tip': '탐색기에서 보기', html: ic('reveal') }), h('button', { class: 'ibtn danger', type: 'button', 'data-a': 'recycle', 'data-tip': '휴지통으로', html: ic('trash') })),
            h('span', { class: 'pstripe' }));
          row._it = it;
          regRow(it, row);
          rows.append(row);
        });
        card.append(rows);
        if (gi < 12 && !reduced) card.style.animation = `row-in .42s var(--ease-out) ${gi * 24}ms both`;
        box.append(card);
      });
    } catch (e) { box.textContent = ''; box.append(h('div', { class: 'empty' }, h('h3', { text: '중복 파일을 불러오지 못했어요' }), h('p', { text: e.message }))); }
  }
  box.addEventListener('click', e => {
    if (Drag.suppressClick) return;
    const row = e.target.closest('.row');
    if (!row || !row._it) return;
    const a = e.target.closest('[data-a]');
    if (a) { if (a.dataset.a === 'reveal') reveal(row._it.path); else confirmRecycle(S.sel.has(row._it.key) ? Sel.items() : [row._it]); return; }
    Sel.toggle(row._it);
  });
  box.addEventListener('contextmenu', e => { const row = e.target.closest('.row'); if (!row || !row._it) return; e.preventDefault(); rowMenu(row._it, e.clientX, e.clientY); });
  box.addEventListener('pointerdown', e => { const row = e.target.closest('.row'); if (row && row._it) Drag.arm(e, row._it, row); });
  load();
  return {
    el, update() {},
    reload: load,
    removeKeys(keys, how) { keys.forEach(k => { const r = REG.byKey.get(k); if (r && r.el) { r.el.classList.add(how, 'gone'); setTimeout(() => r.el.remove(), 380); } if (r && r.it && r.it._dg) r.it._dg.keys.delete(k); unregRow(k); }); },
    refresh() { setTimeout(load, 500); },
    /* Ctrl+A: 묶음마다 맨 위 사본 하나는 남기고 나머지를 고른다. 「내용 다름」 묶음은 진짜 중복이 아니라 고르지 않는다 */
    selectAll() {
      const keep = [], pick = [];
      let diff = 0;
      for (const g of dgs) {
        const left = g.its.filter(it => g.keys.has(it.key) && !it._pending);
        if (g.verified === false) { if (left.length > 1) diff++; keep.push(...left); continue; }
        if (left.length < 2) { keep.push(...left); continue; }
        keep.push(left[0]);
        pick.push(...left.slice(1));
      }
      Sel.set(keep, false);
      Sel.set(pick, true);
      const picked = pick.filter(it => S.sel.has(it.key));
      const tail = diff ? ` · 내용이 다른 ${fmtNum(diff)}묶음은 뺐어요` : '';
      if (picked.length) Toast.show({ kind: 'info', title: '묶음마다 사본 하나씩 남기고 골랐어요', sub: `${fmtNum(picked.length)}개 · ${fmtBytes(sum(picked.map(i => i.size)))} · 맨 위 사본을 남겨요${tail}`, timeout: 3600 });
      else Toast.show({ kind: 'info', title: '고를 사본이 없어요', sub: diff ? `내용이 다른 ${fmtNum(diff)}묶음은 고르지 않아요` : null, timeout: 2600 });
    },
  };
}

/* ═════════════════════════════════════════════ 행 메뉴 · 탐색기 */
async function reveal(path) {
  try { await POST('/api/reveal', { path }); } catch (e) { Toast.err(e, '탐색기를 열지 못했어요'); }
}
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); return true; } catch (e) {
    const ta = h('textarea', { style: { position: 'fixed', opacity: '0', left: '-9999px' } });
    ta.value = t; document.body.append(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e2) { ok = false; }
    ta.remove();
    return ok;
  }
}
function rowMenu(it, x, y) {
  const multi = S.sel.has(it.key) && S.sel.size > 1;
  const targets = multi ? Sel.items() : [it];
  const items = [];
  if (multi) items.push({ head: `${fmtNum(targets.length)}개 선택 · ${fmtBytes(sum(targets.map(t => t.size)))}` });
  items.push({ label: '탐색기에서 보기', icon: 'reveal', onClick: () => reveal(it.path) });
  items.push({ label: '경로 복사', icon: 'copy', onClick: async () => { const ok = await copyText(targets.map(t => t.path).join('\r\n')); Toast.show({ kind: ok ? 'ok' : 'err', title: ok ? '경로를 복사했어요' : '복사하지 못했어요', sub: ok ? (targets.length > 1 ? `${targets.length}개` : it.path) : null, timeout: 2600 }); } });
  if (it.kind === 'dir' && !multi && it.id !== 0) {
    items.push('-');
    const short = it.name.length > 16 ? it.name.slice(0, 15) + '…' : it.name;
    items.push({
      label: '이 폴더 분류 바꾸기', icon: 'tag', right: short,
      sub: S.cats.filter(c => c.id !== 9).map(c => ({ label: c.name, dot: `var(--c${c.id})`, onClick: () => addRule(it.path, c.id) })),
    });
  }
  items.push('-');
  const prot = targets.some(t => t.protected);
  const allSame = d => targets.every(t => letterOfPath(t.path) === d.letter);
  const targetMenu = mode => {
    const out = [];
    for (const g of GROUPS) {
      const ds = liveDrives().filter(d => driveGroup(d) === g.id && (mode === 'copy' || !allSame(d)));
      if (!ds.length) continue;
      if (out.length) out.push('-');
      ds.forEach(d => out.push({ label: dname(d), icon: driveIcon(d), right: allSame(d) ? '폴더 선택' : `${fmtBytes(d.free)} 남음`,
        onClick: () => { if (allSame(d)) pickAndConfirm(targets, mode, d.root); else confirmMove(targets, d.letter, { mode }); } }));
    }
    out.push('-');
    out.push({ label: '폴더 선택…', icon: 'folderPlus', onClick: () => pickAndConfirm(targets, mode, null) });
    return out;
  };
  items.push({ label: '다른 위치로 이동…', icon: 'move', disabled: prot, sub: prot ? null : targetMenu('move') });
  items.push({ label: '다른 위치로 복사…', icon: 'copy', disabled: prot, sub: prot ? null : targetMenu('copy') });
  const rm = Tray.recycleMode(targets);
  items.push({ label: rm === 'delete' ? '삭제(복구 불가)' : '휴지통으로 보내기', icon: 'trash', danger: true, disabled: prot, tip: prot ? (it.protected_reason || '보호된 항목이에요.') : null, onClick: () => confirmRecycle(targets) });
  Menu.open({ x, y }, items);
}
/* 폴더를 고른 뒤 확인 시트로(브라우저 모드면 경로 입력 팝오버) */
async function pickAndConfirm(items, mode, initial) {
  let r;
  try { r = await pickFolderNative(initial); } catch (e) { Toast.err(e, '폴더 선택 창을 열지 못했어요'); return; }
  if (r.canceled) return;
  if (r.inline) {
    const anchor = Tray.shown ? $(mode === 'copy' ? '#trCopy' : '#trMove') : { x: innerWidth / 2 - 210, y: innerHeight / 2 - 120 };
    MovePop.open(anchor, items, { mode, place: 'top' });
    MovePop.pick(initial);
    return;
  }
  confirmMove(items, null, { mode, destDir: r.path });
}
function driveMenu(anchor, d) {
  if (!d) return;
  const conn = isConn(d);
  const items = [];
  if (conn) items.push({ label: '탐색기에서 열기', icon: 'reveal', onClick: () => reveal(d.root) });
  if (conn) items.push({ label: '「모두 스캔」에 넣기', check: !!d.scan, onClick: () => setScanTarget(d.letter, !d.scan).catch(e => Toast.err(e)) });
  items.push({ label: '드라이브 스캔 설정…', icon: 'gear', onClick: () => openSettings('drives') });
  if (d.kind === 'network' && /^N-/i.test(d.letter)) { items.push('-'); items.push({ label: 'NAS 위치 지우기', icon: 'x', danger: true, onClick: () => confirmRemoveNas(d) }); }
  if (!conn) { items.push('-'); items.push({ label: '기록 지우기', icon: 'trash', danger: true, onClick: () => confirmForget(d) }); }
  Menu.open(anchor, items, { place: 'bottom-end' });
}
async function addRule(path, cat) {
  try {
    const r = await POST('/api/rules', { path, cat });
    const st = r.settings || null;
    if (st) S.settings = st;
    Toast.show({ kind: 'ok', title: `「${nameOf(path)}」을 ${catName(cat)}(으)로 분류했어요`, sub: '이 폴더 아래 모든 파일에 적용돼요. 설정에서 지울 수 있어요.' });
    Poll.kick();
    Recs.load();
    setTimeout(() => { if (S.screen && S.screen.refresh) S.screen.refresh('rule'); }, 300);
  } catch (e) { Toast.err(e, '분류를 바꾸지 못했어요'); }
}

/* ═════════════════════════════════════════════ NAS 추가 (SPEC-2 A.1) */
/* 장치 이름만 넣으면 공유 목록을 찾아 체크로 고른다. 못 찾으면 \\장치\공유 를 직접 입력. */
function NasAdder(o = {}) {
  const host = h('input', { class: 'tin', type: 'text', spellcheck: 'false', autocomplete: 'off', placeholder: '\\\\ORICO  또는  192.168.0.10' });
  const findB = h('button', { class: 'btn', type: 'button', html: ic('search') + '<span>공유 찾기</span>' });
  const res = h('div', { class: 'nas-res' });
  const manualL = h('button', { class: 'link', type: 'button', text: '공유 이름을 직접 입력할게요' });
  const el = h('div', { class: 'nas-add' },
    h('div', { class: 'lab2', text: '장치 이름 또는 IP' }),
    h('div', { class: 'addrow' }, host, findB),
    res,
    h('div', { class: 'nas-man-l' }, manualL));
  const st = { mode: 'idle', shares: [], pick: new Set(), host: '', label: '', manual: '', busy: false };
  const labelIn = h('input', { class: 'tin', type: 'text', spellcheck: 'false', placeholder: '화면에 보일 이름' });
  labelIn.addEventListener('input', () => { st.label = labelIn.value; });
  const manIn = h('input', { class: 'tin', type: 'text', spellcheck: 'false', placeholder: '\\\\ORICO\\공유이름' });
  manIn.addEventListener('input', () => { st.manual = manIn.value; sync(); });
  manIn.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); api.add(); } });
  const hostName = v => String(v || '').trim().replace(/^[\\/]+/, '').split(/[\\/]/)[0];
  const isUnc = v => /^[\\/]{2}[^\\/]+[\\/][^\\/]+/.test(String(v || '').trim());
  function sync() { if (o.onChange) o.onChange(api.ready()); }
  function defLabel(name) { const hn = st.host || hostName(manIn.value); return hn ? `${hn} · ${name}` : name; }
  function render() {
    res.textContent = '';
    manualL.hidden = st.mode === 'manual';
    if (st.mode === 'loading') { res.append(h('div', { class: 'loadmore' }, icEl('spinner', 'spin'), h('span', { text: '공유 폴더를 찾는 중이에요…' }))); return; }
    if (st.mode === 'shares') {
      const list = h('div', { class: 'shlist' });
      st.shares.forEach(sh => {
        const on = st.pick.has(sh.unc);
        const r = h('button', { class: 'shrow' + (on ? ' on' : '') + (sh.added ? ' added' : ''), type: 'button', disabled: sh.added ? true : null },
          h('span', { class: 'ck' + (on ? ' on' : ''), html: ic('check') }), h('span', { class: 'tile t-sm', 'data-c': 'drive', html: ic('nas') }),
          h('div', { class: 'shn' }, h('b', { text: sh.name }), h('span', { text: sh.unc })),
          sh.added ? h('span', { class: 'kchip', text: '추가됨' }) : null);
        r.addEventListener('click', () => { if (st.pick.has(sh.unc)) st.pick.delete(sh.unc); else st.pick.add(sh.unc); render(); sync(); });
        list.append(r);
      });
      res.append(h('div', { class: 'lab2', text: `${st.host} 의 공유 폴더 ${st.shares.length}개` }), list);
      const one = [...st.pick];
      if (one.length === 1) {
        const sh = st.shares.find(x => x.unc === one[0]);
        if (!st.label || st._labFor !== one[0]) { st.label = defLabel(sh ? sh.name : ''); st._labFor = one[0]; labelIn.value = st.label; }
        res.append(h('div', { class: 'lab2', text: '이름' }), labelIn);
      } else if (one.length > 1) res.append(h('div', { class: 'hint2', text: `${one.length}개를 「${st.host} · 공유 이름」으로 추가해요.` }));
      if (!st.shares.length) res.append(note('', 'info', '보이는 공유 폴더가 없어요. 공유 이름을 직접 입력해 주세요.'));
      return;
    }
    if (st.mode === 'manual') {
      if (st.err) res.append(note('warn', 'warn', st.err));
      res.append(h('div', { class: 'lab2', text: '네트워크 경로' }), manIn, h('div', { class: 'lab2', text: '이름(선택)' }), labelIn);
      return;
    }
    if (st.err) res.append(note('warn', 'warn', st.err));
  }
  async function find() {
    const v = host.value.trim();
    if (!v) { host.focus(); return; }
    if (isUnc(v)) { st.mode = 'manual'; manIn.value = v; st.manual = v; st.err = ''; render(); sync(); return; }
    st.mode = 'loading'; st.err = ''; render(); sync();
    findB.disabled = true;
    try {
      const r = await POST('/api/locations/shares', { host: v });
      st.host = r.host || hostName(v);
      st.shares = r.shares || [];
      st.pick = new Set(st.shares.filter(x => !x.added).length === 1 ? [st.shares.find(x => !x.added).unc] : []);
      st.label = ''; st._labFor = null;
      st.mode = 'shares';
    } catch (e) {
      st.mode = 'manual';
      st.err = e.message;
      const hn = hostName(v);
      if (hn && !manIn.value) { manIn.value = `\\\\${hn}\\`; st.manual = manIn.value; }
      setTimeout(() => { manIn.focus(); const n = manIn.value.length; try { manIn.setSelectionRange(n, n); } catch (e2) { /* */ } }, 30);
    } finally { findB.disabled = false; }
    render(); sync();
  }
  findB.addEventListener('click', find);
  host.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); find(); } });
  manualL.addEventListener('click', () => { st.mode = 'manual'; st.err = ''; const hn = hostName(host.value); if (hn && !manIn.value) manIn.value = `\\\\${hn}\\`; st.manual = manIn.value; render(); sync(); setTimeout(() => manIn.focus(), 30); });
  const api = {
    el,
    focus() { setTimeout(() => host.focus(), 60); },
    ready() { return !st.busy && ((st.mode === 'shares' && st.pick.size > 0) || (st.mode === 'manual' && isUnc(manIn.value))); },
    async add() {
      if (!api.ready()) return false;
      st.busy = true; sync();
      const todo = st.mode === 'shares'
        ? [...st.pick].map(u => { const sh = st.shares.find(x => x.unc === u); return { path: u, label: st.pick.size === 1 ? (labelIn.value.trim() || defLabel(sh.name)) : defLabel(sh.name) }; })
        : [{ path: manIn.value.trim(), label: labelIn.value.trim() || null }];
      const added = [], errs = [];
      for (const t of todo) {
        try { const r = await POST('/api/locations/add', t); added.push(r); if (r.settings) S.settings = r.settings; } catch (e) {
          if (e.code === 'need_share') { st.mode = 'manual'; st.err = e.message; manIn.value = `\\\\${e.data.host || hostName(t.path)}\\`; }
          errs.push(e);
        }
      }
      st.busy = false;
      render(); sync();
      if (added.length) {
        const a = added[0];
        Toast.show({
          kind: a.connected ? 'ok' : 'info',
          title: added.length > 1 ? `NAS 위치 ${added.length}개를 추가했어요` : `${josa(a.label, '을', '를')} 추가했어요`,
          sub: a.message || '「모두 스캔」에는 넣지 않았어요. 카드의 [스캔] 으로 스캔할 수 있어요.',
          actions: a.connected ? [{ label: '지금 스캔', primary: true, onClick: () => startScan(added.map(x => x.key)) }] : [],
        });
        Poll.kick();
        setTimeout(() => added.forEach(x => pulseRail(x.key)), 700);
        if (o.onAdded) o.onAdded(added);
      }
      if (errs.length && !added.length) { const e = errs[0]; if (e.code !== 'need_share') { st.err = e.message; if (st.mode !== 'manual') { st.mode = st.mode === 'shares' ? 'shares' : 'manual'; } render(); } return false; }
      return added.length > 0;
    },
  };
  render();
  return api;
}
function openNasSheet() {
  const add = h('button', { class: 'btn primary', type: 'button', html: ic('plus') + '<span>추가</span>', disabled: true });
  const cancel = h('button', { class: 'btn ghost', type: 'button', text: '취소' });
  const adder = NasAdder({ onChange: ready => { add.disabled = !ready; }, onAdded: () => sh.close() });
  const body = h('div', {}, adder.el,
    note('', 'info', h('b', { text: 'ORICO NAS 는 SMB 공유예요. ' }), '탐색기 「네트워크」에서 장치를 열면 녹색 띠가 있는 공유 폴더 아이콘으로 보여요. 경로는 \\\\장치이름\\공유이름 이에요. NAS 에는 휴지통이 없어서 지우면 바로 영구 삭제돼요.'));
  const sh = Sheet.open({ icon: tile('drive', 'nas', 't-lg'), title: 'NAS 추가', sub: '장치 이름(예: ORICO)이나 IP 만 넣으면 공유 폴더를 찾아 드려요.', body, cls: 'nas' });
  sh.primary = add;
  sh.setFoot([h('span', { class: 'hint', text: '추가한 위치는 설정에서 지울 수 있어요' }), h('span', { class: 'sp' }), cancel, add]);
  cancel.addEventListener('click', () => sh.close());
  add.addEventListener('click', async () => { add.disabled = true; const ok = await adder.add(); if (!ok && !sh.closed) { sh.shake(); } });
  adder.focus();
}
function confirmRemoveNas(d, after) {
  const go = h('button', { class: 'btn danger', type: 'button', html: ic('x') + '<span>위치 지우기</span>' });
  const cancel = h('button', { class: 'btn ghost', type: 'button', text: '취소' });
  const sh = Sheet.open({
    icon: tile('drive', 'nas', 't-lg'), title: `${dname(d)} 위치를 지울까요?`, sub: d.unc || '',
    body: h('div', {}, note('', 'info', h('b', { text: '목록과 저장된 스캔 기록만 지워요. ' }), 'NAS 안의 파일은 그대로예요. 나중에 다시 추가할 수 있어요.')),
  });
  sh.primary = go;
  sh.setFoot([h('span', { class: 'sp' }), cancel, go]);
  cancel.addEventListener('click', () => sh.close());
  go.addEventListener('click', async () => {
    go.disabled = true; sh.busy = true;
    try {
      const r = await POST('/api/locations/remove', { key: d.letter });
      if (r.settings) S.settings = r.settings;
      sh.busy = false; sh.close();
      Toast.show({ kind: 'ok', title: `${dname(d)} 위치를 지웠어요` });
      if (S.route.name === 'drive' && S.route.letter === d.letter) go_home();
      Poll.kick();
      if (after) after();
    } catch (e) { sh.busy = false; go.disabled = false; sh.shake(); sh.body.append(note('danger', 'warn', e.message)); }
  });
}

/* ═════════════════════════════════════════════ 설정 */
function stepper(val, min, max, step, unit, onChange, dec = 0) {
  const fmt = v => (dec ? Number(v).toFixed(dec).replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1') : String(v));
  const inp = h('input', { type: 'text', inputmode: dec ? 'decimal' : 'numeric', value: fmt(val) });
  const set = v => {
    let n = Number(String(v).replace(',', '.'));
    if (!isFinite(n)) n = min;
    n = dec ? Math.round(n / step) * step : Math.round(n);
    n = clamp(Number(n.toFixed(dec || 0)), min, max);
    inp.value = fmt(n); onChange(n);
  };
  const el = h('div', { class: 'stepper' },
    h('button', { type: 'button', 'aria-label': '줄이기', html: ic('minus'), onclick: () => set(Number(inp.value) - step) }),
    inp, h('span', { class: 'u', text: unit }),
    h('button', { type: 'button', 'aria-label': '늘리기', html: ic('plus'), onclick: () => set(Number(inp.value) + step) }));
  inp.addEventListener('change', () => set(inp.value));
  inp.addEventListener('keydown', e => { if (e.key === 'ArrowUp') { e.preventDefault(); set(Number(inp.value) + step); } if (e.key === 'ArrowDown') { e.preventDefault(); set(Number(inp.value) - step); } });
  return el;
}
const isAbsPath = v => /^[A-Za-z]:[\\/]/.test(v) || /^[\\/]{2}[^\\/]+[\\/][^\\/]/.test(v) || v.startsWith('/');
function pathList(arr, placeholder, o = {}) {
  const wrap = h('div', {});
  const list = h('div', { class: 'plist' });
  const inp = h('input', { class: 'tin', type: 'text', placeholder, spellcheck: 'false' });
  const add = h('button', { class: 'btn', type: 'button', html: ic('plus') + '<span>추가</span>' });
  const pickB = S.meta && S.meta.dialog ? h('button', { class: 'ibtn', type: 'button', 'data-tip': '폴더 고르기', html: ic('folder') }) : null;
  const render = () => {
    list.textContent = '';
    if (!arr.length) list.append(h('div', { class: 'empty-s', text: o.empty || '없음' }));
    arr.forEach((p, i) => list.append(h('div', { class: 'pi' }, h('span', { class: 'p' }, pathEl(p, 70)), h('span'),
      h('button', { class: 'ibtn xs danger', type: 'button', 'data-tip': o.removeTip || '빼기', html: ic('x'), onclick: async () => {
        if (o.onRemove) { try { await o.onRemove(p); } catch (e) { Toast.err(e, '빼지 못했어요'); return; } }
        arr.splice(arr.indexOf(p), 1); render(); if (o.onChange) o.onChange();
      } }))));
  };
  const doAdd = async v => {
    v = (v == null ? inp.value : v).trim();
    if (!v) return;
    if (!isAbsPath(v)) { inp.focus(); Toast.show({ kind: 'err', title: '전체 경로를 입력해 주세요', sub: '예: D:\\Projects\\Cache', timeout: 3000 }); return; }
    if (arr.some(x => pk(x) === pk(v))) { inp.value = ''; return; }
    if (o.onAdd) { try { const r = await o.onAdd(v); if (typeof r === 'string') v = r; } catch (e) { Toast.err(e, '추가하지 못했어요'); return; } }
    arr.push(v);
    inp.value = '';
    render();
    if (o.onChange) o.onChange();
  };
  add.addEventListener('click', () => doAdd());
  if (pickB) pickB.addEventListener('click', async () => { try { const r = await pickFolderNative(null); if (r.path) doAdd(r.path); } catch (e) { Toast.err(e); } });
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); doAdd(); } });
  wrap.append(list, h('div', { class: 'addrow' + (pickB ? ' three' : '') }, inp, pickB, add));
  render();
  wrap.rerender = render;
  return wrap;
}
/* 이니셜↔이름, 별칭→광고주 같은 짝 목록 */
function pairEditor(obj, o) {
  const wrap = h('div', { class: 'pairs' });
  const rows = h('div', { class: 'pr-rows' });
  const head = h('div', { class: 'pr-h' }, h('span', { text: o.kLabel }), h('span'), h('span', { text: o.vLabel }), h('span'));
  const list = Object.entries(obj).map(([k, v]) => ({ k, v }));
  const commit = () => {
    const out = {};
    list.forEach(x => { const k = (o.upper ? x.k.toUpperCase() : x.k).trim(); const v = x.v.trim(); if (k && v) out[k] = v; });
    Object.keys(obj).forEach(k => delete obj[k]);
    Object.assign(obj, out);
    if (o.onChange) o.onChange();
  };
  const rowEl = x => {
    const ki = h('input', { class: 'tin', type: 'text', spellcheck: 'false', placeholder: o.kPh, value: x.k });
    const vi = h('input', { class: 'tin', type: 'text', spellcheck: 'false', placeholder: o.vPh, value: x.v });
    if (o.upper) ki.classList.add('up');
    ki.addEventListener('input', () => { x.k = ki.value; commit(); });
    vi.addEventListener('input', () => { x.v = vi.value; commit(); });
    const del = h('button', { class: 'ibtn xs danger', type: 'button', 'data-tip': '지우기', html: ic('x'), onclick: () => { list.splice(list.indexOf(x), 1); r.remove(); commit(); empty(); } });
    const r = h('div', { class: 'pr' }, ki, h('span', { class: 'pr-a', html: ic('arrowR') }), vi, del);
    return { r, ki };
  };
  const emptyEl = h('div', { class: 'empty-s', text: o.empty });
  const empty = () => { emptyEl.hidden = !!list.length; head.hidden = !list.length; };
  list.forEach(x => rows.append(rowEl(x).r));
  const add = h('button', { class: 'btn sm', type: 'button', html: ic('plus') + `<span>${o.addLabel}</span>`, onclick: () => { const x = { k: '', v: '' }; list.push(x); const e = rowEl(x); rows.append(e.r); empty(); e.ki.focus(); } });
  wrap.append(head, rows, emptyEl, h('div', { class: 'pr-add' }, add));
  empty();
  return wrap;
}
const SET_SECS = [
  { id: 'general', label: '일반', icon: 'gear' },
  { id: 'drives', label: '드라이브', icon: 'storage' },
  { id: 'nas', label: 'NAS', icon: 'nas' },
  { id: 'projects', label: '프로젝트', icon: 'proj' },
  { id: 'previews', label: '미리보기', icon: 'play' },
  { id: 'team', label: '팀 설정', icon: 'people' },
  { id: 'update', label: '업데이트', icon: 'ext2' },
];
const SET_ALIAS = { workers: 'projects', aliases: 'projects', clients: 'projects', roots: 'projects', project_roots: 'projects', cache: 'previews', preview: 'previews', scan: 'drives', drive_scan: 'drives', network: 'nas', locations: 'nas', import: 'team', export: 'team', updates: 'update' };
async function openSettings(section) {
  if (section && SET_ALIAS[section]) section = SET_ALIAS[section];
  let st;
  try { const r = await GET('/api/settings'); st = r.settings || r; } catch (e) { st = S.settings || {}; }
  S.settings = st;
  const draft = {
    big_file_mb: Number(st.big_file_mb) || 50, verify: st.verify || 'quick', old_days: Number(st.old_days) || 180,
    excluded: [...(st.excluded || [])], cache_paths: [...(st.cache_paths || [])],
    workers: Object.assign({}, st.workers || {}), client_aliases: Object.assign({}, st.client_aliases || {}),
    preview_cache_gb: Number(st.preview_cache_gb) || 2,
  };
  const base = JSON.stringify(draft);
  const save = h('button', { class: 'btn primary', type: 'button', html: ic('check') + '<span>저장</span>' });
  const dirtyHint = h('span', { class: 'hint' });
  const dirty = () => { const d = JSON.stringify(draft) !== base; save.disabled = !d; dirtyHint.textContent = d ? '저장하지 않은 변경이 있어요' : (S.meta ? `DriveBoard ${S.meta.version || ''}` : ''); dirtyHint.classList.toggle('dirty', d); };
  const rules = [...(st.rules || [])];
  const vdesc = h('div', { class: 'vdesc' });
  const vt = Tabs(VERIFY.map(v => ({ id: v.id, label: v.label })), draft.verify, id => { draft.verify = id; vdesc.textContent = VERIFY.find(v => v.id === id).desc; dirty(); }, 'seg3');
  vdesc.textContent = VERIFY.find(v => v.id === draft.verify).desc;
  const rulesBox = h('div', { class: 'plist' });
  const renderRules = () => {
    rulesBox.textContent = '';
    if (!rules.length) rulesBox.append(h('div', { class: 'empty-s', text: '아직 없어요. 목록에서 폴더를 우클릭해 「분류 바꾸기」로 추가해요.' }));
    rules.forEach(r => {
      const c = Number(r.cat);
      rulesBox.append(h('div', { class: 'pi' }, h('span', { class: 'p' }, pathEl(r.path, 60)),
        h('span', { class: 'chip', 'data-c': 'c' + c }, h('i'), catName(c)),
        h('button', {
          class: 'ibtn xs danger', type: 'button', 'data-tip': '규칙 지우기(그 폴더를 다시 스캔해요)', html: ic('x'),
          onclick: async () => {
            try {
              const res = await POST('/api/rules/delete', { path: r.path });
              const i = rules.indexOf(r); if (i >= 0) rules.splice(i, 1);
              if (res.settings) S.settings = res.settings;
              renderRules();
              if (res.job_id) Jobs.track(res.job_id, { kind: 'rescan' });
              Toast.show({ kind: 'ok', title: '분류 규칙을 지웠어요', sub: '그 폴더를 다시 스캔해서 원래 분류로 돌려요.' });
            } catch (e) { Toast.err(e, '규칙을 지우지 못했어요'); }
          },
        })));
    });
  };
  renderRules();
  const row = (l, d, ctl) => h('div', { class: 'set-row' }, h('div', {}, h('div', { class: 'l', text: l }), d ? h('div', { class: 'd', text: d }) : null), ctl || h('span'));
  const sub = (l, d, ctl, extra, tag) => h('div', { class: 'set-sec' }, row(l, d, ctl), extra || null, tag ? h('span', { class: 'set-tag', text: tag }) : null);
  const oldChips = h('div', { class: 'chips', style: { marginTop: '10px' } });
  const oldStep = stepper(draft.old_days, 7, 3650, 30, '일', v => { draft.old_days = v; syncOld(); dirty(); });
  const syncOld = () => $$('.chip', oldChips).forEach(c => c.classList.toggle('on', Number(c.dataset.v) === draft.old_days));
  [90, 180, 365, 730].forEach(v => oldChips.append(h('button', { class: 'chip', type: 'button', 'data-v': v, text: ageText(v), onclick: () => { draft.old_days = v; $('input', oldStep).value = String(v); syncOld(); dirty(); } })));
  syncOld();

  // ── 드라이브 스캔 대상(바로 적용)
  const scanList = h('div', { class: 'scanlist' });
  const renderScan = () => {
    scanList.textContent = '';
    for (const g of GROUPS) {
      const ds = S.drives.filter(d => driveGroup(d) === g.id);
      if (!ds.length) continue;
      scanList.append(h('div', { class: 'sl-g', text: g.label }));
      ds.forEach(d => {
        const conn = isConn(d);
        const sw = Switch(!!d.scan, v => setScanTarget(d.letter, v), { label: dname(d) + ' 스캔 대상' });
        const meta = !conn ? '연결 안 됨' + (d.scanned_at ? ` · 마지막 스캔 ${relTime(d.scanned_at)}` : '') : d.scanned_at ? `${relTime(d.scanned_at)} 스캔 · ${fmtBytes(d.total)}` : `아직 스캔 안 함 · ${fmtBytes(d.total)}`;
        const r = h('div', { class: 'sl-r' + (conn ? '' : ' off') }, tile('drive', driveIcon(d), 't-sm'),
          h('div', { class: 'sl-n' }, h('b', { text: dname(d) }), h('span', { class: 'num', text: meta })),
          kindLabel(d) ? h('span', { class: 'kchip', text: kindLabel(d) }) : h('span'),
          conn ? sw.el : h('button', { class: 'btn sm ghost', type: 'button', text: '기록 지우기', onclick: () => { Sheet.close(true); confirmForget(d); } }));
        scanList.append(r);
      });
    }
  };
  renderScan();

  // ── NAS 위치
  const nasList = h('div', { class: 'plist nas-list' });
  const renderNas = () => {
    nasList.textContent = '';
    const locs = (S.settings.network_locations || []);
    if (!locs.length) nasList.append(h('div', { class: 'empty-s', text: '추가한 NAS 위치가 없어요.' }));
    locs.forEach(loc => {
      const d = S.drives.find(x => x.unc && pk(x.unc) === pk(loc.path)) || null;
      const conn = d && isConn(d);
      const del = h('button', { class: 'btn sm ghost', type: 'button', text: '지우기' });
      let armed = false;
      del.addEventListener('click', async () => {
        if (!armed) { armed = true; del.textContent = '정말 지울까요?'; del.className = 'btn sm danger'; setTimeout(() => { if (del.isConnected && armed) { armed = false; del.textContent = '지우기'; del.className = 'btn sm ghost'; } }, 3500); return; }
        del.disabled = true;
        try {
          const key = d ? d.letter : null;
          if (!key) throw new Error('위치를 찾지 못했어요. 잠시 뒤에 다시 해 주세요.');
          const r = await POST('/api/locations/remove', { key });
          if (r.settings) S.settings = r.settings;
          Toast.show({ kind: 'ok', title: `${loc.label || loc.path} 위치를 지웠어요`, sub: 'NAS 안의 파일은 그대로예요.' });
          Poll.kick();
          renderNas();
        } catch (e) { del.disabled = false; Toast.err(e, '지우지 못했어요'); }
      });
      nasList.append(h('div', { class: 'pi nas' }, h('span', { class: 'tile t-sm', 'data-c': 'drive', html: ic('nas') }),
        h('div', { class: 'sl-n' }, h('b', { text: loc.label || loc.path }), h('span', {}, pathEl(loc.path, 50))),
        h('span', { class: 'st-dot ' + (conn ? 'on' : 'off'), text: conn ? '연결됨' : '연결 안 됨' }), del));
    });
  };
  renderNas();
  const nasAddB = h('button', { class: 'btn', type: 'button', html: ic('plus') + '<span>추가</span>', disabled: true });
  const adder = NasAdder({ onChange: r => { nasAddB.disabled = !r; }, onAdded: () => { setTimeout(() => { renderNas(); renderScan(); }, 500); } });
  nasAddB.addEventListener('click', () => adder.add());
  const nasBox = h('div', { class: 'nas-inline' }, adder.el, h('div', { class: 'pr-add' }, nasAddB));

  // ── 프로젝트
  const roots = [...(st.project_roots || [])];
  const excl = [...(st.project_excluded || [])];
  const rootsList = pathList(roots, '예: D:\\Projects', {
    empty: '없어요. 비워 두면 날짜로 시작하는 폴더·AE/프리미어 프로젝트가 있는 폴더를 찾아요.',
    onAdd: async v => { const r = await POST('/api/projects/roots', { add: v }); if (r.settings) S.settings = r.settings; ProjCount.load(); Toast.show({ kind: 'ok', title: '프로젝트 폴더를 추가했어요', sub: '그 폴더의 바로 아래 폴더를 프로젝트로 봐요.', timeout: 3000 }); const nr = (r.settings && r.settings.project_roots) || []; return nr[nr.length - 1] || v; },
    onRemove: async p => { const r = await POST('/api/projects/roots', { remove: p }); if (r.settings) S.settings = r.settings; ProjCount.load(); },
  });
  const exclList = h('div', { class: 'plist' });
  const renderExcl = () => {
    exclList.textContent = '';
    if (!excl.length) exclList.append(h('div', { class: 'empty-s', text: '없어요. 프로젝트 화면에서 「이 폴더는 프로젝트가 아님」을 고르면 여기에 들어가요.' }));
    excl.forEach(p => exclList.append(h('div', { class: 'pi' }, h('span', { class: 'p' }, pathEl(p, 70)), h('span'),
      h('button', { class: 'ibtn xs', type: 'button', 'data-tip': '다시 프로젝트로 보기', html: ic('undo'), onclick: async () => {
        try { const r = await POST('/api/projects/not_project', { path: p, remove: true }); if (r.settings) S.settings = r.settings; excl.splice(excl.indexOf(p), 1); renderExcl(); ProjCount.load(); } catch (e) { Toast.err(e, '되돌리지 못했어요'); }
      } }))));
  };
  renderExcl();
  const workers = pairEditor(draft.workers, { kLabel: '이니셜', vLabel: '이름', kPh: 'KMS', vPh: '이름', upper: true, addLabel: '작업자 추가', empty: '아직 없어요. 파일 이름의 작업자 이니셜(예: KMS)을 이름으로 보여 드려요.', onChange: dirty });
  const aliases = pairEditor(draft.client_aliases, { kLabel: '파일 이름에 쓰인 이름', vLabel: '광고주', kPh: '한결', vPh: '한결증권', addLabel: '별칭 추가', empty: '아직 없어요. 같은 광고주를 다르게 쓴 이름을 하나로 묶어요.', onChange: dirty });
  const orgBtn = window.Projects && typeof window.Projects.openOrganizer === 'function'
    ? h('button', { class: 'link', type: 'button', html: '광고주 정리 열기' + ic('chevR'), onclick: () => { Sheet.close(true); go({ name: 'projects' }); setTimeout(() => { try { window.Projects.openOrganizer(); } catch (e) { /* */ } }, 300); } }) : null;

  // ── 미리보기 캐시
  const pv = S.previews || {};
  const cacheBar = h('div', { class: 'meter2' }, h('i'));
  const cacheTx = h('span', { class: 'num' });
  const paintCache = (bytes, limit) => {
    const p = limit ? Math.min(1, (bytes || 0) / limit) : 0;
    cacheBar.firstChild.style.width = (p * 100).toFixed(1) + '%';
    cacheTx.textContent = `${fmtBytes(bytes || 0)} 사용 · ${fmtNum(pv.count || 0)}개 · 상한 ${fmtBytes(limit || draft.preview_cache_gb * GB)}`;
  };
  paintCache(pv.bytes, pv.limit_bytes || draft.preview_cache_gb * GB);
  const clearB = h('button', { class: 'btn', type: 'button', html: ic('trash') + '<span>미리보기 캐시 비우기</span>' });
  clearB.addEventListener('click', async () => {
    clearB.disabled = true;
    try { const r = await POST('/api/media/clear_cache', {}); pv.count = 0; paintCache(0, pv.limit_bytes); Toast.show({ kind: 'ok', title: `미리보기 캐시를 비웠어요 · ${fmtBytes(r.bytes)}`, sub: '필요할 때 다시 만들어요.' }); } catch (e) { Toast.err(e, '비우지 못했어요'); } finally { clearB.disabled = false; }
  });
  const ffNote = S.meta && S.meta.ffmpeg === false ? note('warn', 'warn', h('b', { text: 'ffmpeg 를 찾지 못했어요. ' }), '직접 재생할 수 있는 영상은 브라우저에서 포스터를 만들고, ProRes 같은 영상은 미리보기를 만들 수 없어요.') : null;

  // ── 팀 설정
  const teamBox = h('div', { class: 'team' });
  const expB = h('button', { class: 'btn', type: 'button', html: ic('export') + '<span>내보내기</span>' });
  const fileIn = h('input', { type: 'file', accept: '.json,application/json', class: 'sr', tabindex: '-1' });
  const impB = h('button', { class: 'btn', type: 'button', html: ic('import') + '<span>가져오기…</span>' });
  const diffBox = h('div', { class: 'tdiff' });
  expB.addEventListener('click', async () => {
    try {
      const r = await fetch('/api/team/export', { headers: { 'X-DB-Token': TOKEN }, cache: 'no-store', credentials: 'same-origin' });
      if (!r.ok) throw new ApiError(r.status, await r.json().catch(() => null));
      const data = await r.json();
      delete data.ok;
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const a = h('a', { href: URL.createObjectURL(blob), download: 'driveboard-team.json' });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      Toast.show({ kind: 'ok', title: '팀 설정을 내보냈어요', sub: `작업자 ${fmtNum(Object.keys(data.workers || {}).length)}명 · 별칭 ${fmtNum(Object.keys(data.client_aliases || {}).length)}개 · 분류 규칙 ${fmtNum((data.rules || []).length)}개` });
    } catch (e) { Toast.err(e, '내보내지 못했어요'); }
  });
  impB.addEventListener('click', () => fileIn.click());
  let pending = null;
  fileIn.addEventListener('change', async () => {
    const f = fileIn.files && fileIn.files[0];
    fileIn.value = '';
    if (!f) return;
    if (f.size > 900 * 1024) { Toast.show({ kind: 'err', title: '파일이 너무 커요' }); return; }
    let text;
    try { text = await f.text(); } catch (e) { Toast.err(e, '파일을 읽지 못했어요'); return; }
    try {
      const r = await POST('/api/team/import', { data: text, apply: false });
      pending = text;
      renderDiff(r.diff, f.name);
    } catch (e) { diffBox.textContent = ''; diffBox.append(note('danger', 'warn', e.message)); }
  });
  function renderDiff(diff, fname) {
    diffBox.textContent = '';
    const W = diff.workers || {}, A = diff.client_aliases || {}, R = diff.rules || {};
    const nW = Object.keys(W.added || {}).length + Object.keys(W.changed || {}).length;
    const nA = Object.keys(A.added || {}).length + Object.keys(A.changed || {}).length;
    const nR = (R.added || []).length + (R.changed || []).length;
    const lines = h('div', { class: 'td-l' });
    const kv = (obj, chg) => Object.entries(obj || {}).slice(0, 6).map(([k, v]) => h('div', { class: 'td-r' }, h('span', { class: 'k', text: k }), icEl('arrowR'), h('span', { text: chg ? `${v[0]} → ${v[1]}` : v })));
    const grp = (title, n, same, kids) => h('div', { class: 'td-g' }, h('div', { class: 'td-h' }, h('b', { text: title }), h('span', { class: 'num', text: n ? `${fmtNum(n)}개 바뀜` : '바뀌는 것 없음' }), same ? h('span', { class: 'tdim num', text: `같음 ${fmtNum(same)}` }) : null), ...kids);
    lines.append(grp('작업자', nW, W.same, [...kv(W.added), ...kv(W.changed, true)]));
    lines.append(grp('광고주 별칭', nA, A.same, [...kv(A.added), ...kv(A.changed, true)]));
    const rr = [...(R.added || []), ...(R.changed || [])].slice(0, 6).map(x => h('div', { class: 'td-r' + (x.exists ? '' : ' miss') }, h('span', { class: 'chip', 'data-c': 'c' + x.cat }, h('i'), catName(x.cat)), pathEl(x.path, 52),
      x.exists ? null : h('span', { class: 'kchip', 'data-tip': '이 PC 에는 이 경로가 없어요(드라이브 문자가 다를 수 있어요). 규칙은 저장되지만 지금은 적용할 곳이 없어요.', text: '이 PC 에 없음' })));
    lines.append(grp('분류 규칙', nR, R.same, rr));
    const total = nW + nA + nR;
    const applyB = h('button', { class: 'btn primary sm', type: 'button', html: ic('check') + '<span>가져오기 적용</span>', disabled: !total });
    const cancelB = h('button', { class: 'btn ghost sm', type: 'button', text: '취소', onclick: () => { diffBox.textContent = ''; pending = null; } });
    applyB.addEventListener('click', async () => {
      applyB.disabled = true;
      try {
        const r = await POST('/api/team/import', { data: pending, apply: true });
        if (r.settings) { S.settings = r.settings; Object.assign(draft.workers, r.settings.workers || {}); Object.assign(draft.client_aliases, r.settings.client_aliases || {}); }
        diffBox.textContent = '';
        diffBox.append(note('lime', 'check', h('b', { text: '가져왔어요. ' }), `작업자 ${nW} · 별칭 ${nA} · 분류 규칙 ${nR}개를 반영했어요. 다시 열면 목록에 보여요.`));
        ProjCount.load(); Recs.load();
      } catch (e) { applyB.disabled = false; diffBox.append(note('danger', 'warn', e.message)); }
    });
    diffBox.append(...[h('div', { class: 'td-top' }, icEl('import'), h('b', { text: fname }), h('span', { class: 'tdim', text: '미리보기 — 아직 저장하지 않았어요' })), lines,
      R.missing_paths ? note('warn', 'info', `분류 규칙 ${fmtNum(R.missing_paths)}개는 이 PC 에 없는 경로예요. 드라이브 문자가 다르면 가져온 뒤 설정에서 지우고 다시 정해 주세요.`) : null,
      note('', 'info', '가져오기는 더하거나 바꾸기만 하고, 이 PC 의 기존 항목을 지우지 않아요.'),
      h('div', { class: 'td-acts' }, h('span', { class: 'sp' }), cancelB, applyB)].filter(Boolean));
  }
  teamBox.append(h('div', { class: 'td-btns' }, expB, impB, fileIn), diffBox);

  // ── 업데이트
  const updSw = Switch(st.update_check !== false, async v => { const r = await POST('/api/settings', { update_check: v }); S.settings = r.settings || S.settings; Poll.kick(); return true; }, { label: '새 버전 확인' });
  const relB = h('button', { class: 'link', type: 'button', html: 'GitHub 릴리스 보기' + ic('ext2'), onclick: () => POST('/api/open_url', { url: (S.meta && S.meta.releases_url) || 'https://github.com/minsk999/driveboard/releases' }).catch(e => Toast.err(e)) });

  const secEls = {
    general: [
      sub('큰 파일 기준', '이 크기 이상인 파일만 목록에 따로 보여요. 작은 파일은 폴더 합계에 들어가요. 바꾸면 다음 스캔부터 반영돼요.', stepper(draft.big_file_mb, 1, 102400, 10, 'MB', v => { draft.big_file_mb = v; dirty(); })),
      sub('이동·복사할 때 검증', null, null, h('div', { style: { marginTop: '12px' } }, vt.el, vdesc)),
      sub('오래된 기준', '「오래된 렌더」 추천에 쓰여요. 수정한 지 이 기간이 지난 항목이에요.', oldStep, oldChips),
    ],
    drives: [
      sub('드라이브 스캔 대상', '「모두 스캔」에 넣을 장치예요. 외장·USB·NAS 는 기본으로 빠져 있어요. 바로 적용돼요.', null, scanList),
      sub('제외 폴더', '스캔하지 않을 폴더예요. 다음 스캔부터 반영돼요.', null, pathList(draft.excluded, '예: D:\\Backup\\옛날', { onChange: dirty })),
      sub('캐시 폴더 추가', '이 폴더 아래는 모두 「캐시·임시」로 셉니다. 예: AE 디스크 캐시를 다른 곳에 둔 경우.', null, pathList(draft.cache_paths, '예: F:\\AE_DiskCache', { onChange: dirty })),
      sub('분류 규칙', '폴더마다 정한 분류예요. 규칙이 확장자보다 먼저 적용돼요.', null, rulesBox),
    ],
    nas: [
      sub('NAS 위치', '드라이브 문자 없이 쓰는 네트워크 공유예요. 지워도 NAS 안의 파일은 그대로예요.', null, nasList),
      sub('NAS 추가', '장치 이름(예: ORICO)이나 IP 를 넣고 [공유 찾기] 를 누르세요.', null, nasBox),
    ],
    projects: [
      sub('작업자', '파일·폴더 이름의 이니셜을 이름으로 보여 드려요.', null, workers),
      sub('광고주 별칭', '같은 광고주를 다르게 쓴 이름을 하나로 묶어요. 대소문자·띄어쓰기는 원래 구분하지 않아요.', orgBtn, aliases),
      sub('프로젝트 폴더', '프로젝트를 모아 둔 폴더예요. 그 바로 아래 폴더를 하나하나 프로젝트로 봐요. 바로 적용돼요.', null, rootsList),
      sub('프로젝트가 아닌 폴더', '프로젝트 목록에서 뺀 폴더예요.', null, exclList),
    ],
    previews: [
      sub('미리보기 캐시 크기', '포스터·재생용 사본을 이 크기까지 보관해요. 넘으면 오래 안 본 것부터 지워요.', stepper(draft.preview_cache_gb, 0.5, 50, 0.5, 'GB', v => { draft.preview_cache_gb = v; dirty(); }, 1),
        h('div', { class: 'cache-u' }, cacheBar, h('div', { class: 'cache-r' }, cacheTx, h('span', { class: 'sp' }), clearB), ffNote)),
    ],
    team: [
      sub('팀 설정 내보내기·가져오기', '광고주 별칭·작업자·분류 규칙을 JSON 파일 하나로 나눠요. 팀원이 같은 광고주 목록을 쓰게 해 줘요. 개인 경로·드라이브 설정은 넣지 않아요.', null, teamBox),
    ],
    update: [
      sub('시작할 때 새 버전 확인', 'GitHub 에서 새 버전이 나왔는지 한 번 확인하고, 있으면 위쪽에 알려 드려요. 자동으로 설치하지는 않아요.', updSw.el,
        h('div', { class: 'upd-r' }, h('span', { class: 'tdim num', text: `지금 버전 ${S.meta && S.meta.version ? 'v' + S.meta.version : ''}` }), S.update && S.update.newer ? h('span', { class: 'kchip lime', text: `새 버전 v${S.update.latest}` }) : null, h('span', { class: 'sp' }), relB)),
    ],
  };
  const navEl = h('nav', { class: 'set-nav' });
  const content = h('div', { class: 'set-body' });
  const navBtns = {};
  SET_SECS.forEach(sx => {
    const b = h('button', { type: 'button', 'data-s': sx.id, html: ic(sx.icon) + `<span>${sx.label}</span>` });
    navBtns[sx.id] = b;
    navEl.append(b);
    content.append(h('section', { class: 'set-grp', 'data-s': sx.id }, h('h4', { text: sx.label }), ...secEls[sx.id]));
  });
  const ind = h('span', { class: 'set-ind' });
  navEl.append(ind);
  const body = h('div', { class: 'set-wrap' }, navEl, content);
  const cancel = h('button', { class: 'btn ghost', type: 'button', text: '닫기' });
  const sh = Sheet.open({ icon: tile('drive', 'gear', 't-lg'), title: '설정', sub: '이 PC 에만 저장돼요. 팀과 나눌 항목은 「팀 설정」에서 내보낼 수 있어요.', body, cls: 'settings' });
  sh.primary = save;
  sh.setFoot([dirtyHint, h('span', { class: 'sp' }), cancel, save]);
  dirty();
  cancel.addEventListener('click', () => sh.close());
  const scroller = sh.body;
  let curSec = '';
  const mark = id => {
    if (id === curSec) return;
    curSec = id;
    Object.entries(navBtns).forEach(([k, b]) => b.classList.toggle('on', k === id));
    const b = navBtns[id];
    if (b) { ind.style.transform = `translateY(${b.offsetTop}px)`; ind.style.height = b.offsetHeight + 'px'; }
  };
  let lockUntil = 0;
  const spy = () => {
    if (performance.now() < lockUntil) return;   // 내비를 눌러 움직이는 중에는 누른 구획을 유지
    const top = scroller.getBoundingClientRect().top;
    let best = SET_SECS[0].id;
    $$('.set-grp', content).forEach(g => { if (g.getBoundingClientRect().top - top < 80) best = g.dataset.s; });
    if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) best = SET_SECS[SET_SECS.length - 1].id;
    mark(best);
  };
  scroller.addEventListener('scroll', spy, { passive: true });
  scroller.addEventListener('wheel', () => { lockUntil = 0; }, { passive: true });
  Object.entries(navBtns).forEach(([k, b]) => b.addEventListener('click', () => {
    const g = $(`.set-grp[data-s="${k}"]`, content);
    lockUntil = performance.now() + 1400;
    if (g) scroller.scrollTo({ top: g.offsetTop - 4, behavior: reduced ? 'auto' : 'smooth' });
    mark(k);
  }));
  requestAnimationFrame(() => {
    mark(SET_SECS[0].id);
    if (section && navBtns[section]) { const g = $(`.set-grp[data-s="${section}"]`, content); if (g) scroller.scrollTop = g.offsetTop - 4; mark(section); }
  });
  save.addEventListener('click', async () => {
    save.disabled = true;
    try {
      const r = await POST('/api/settings', draft);
      S.settings = r.settings || Object.assign({}, S.settings, draft);
      sh.close();
      Toast.show({ kind: 'ok', title: '설정을 저장했어요', sub: '스캔 기준은 다음 스캔부터 반영돼요.' });
      Recs.load(); ProjCount.load();
      if (S.screen && S.screen.refresh) S.screen.refresh('settings');
    } catch (e) { save.disabled = false; sh.shake(); content.prepend(note('danger', 'warn', e.message)); }
  });
}

/* ═════════════════════════════════════════════ 프로젝트 화면(ui/projects.js 를 붙인다) */
function fmtDate(ts) {
  if (ts == null || ts === '') return '';
  if (typeof ts === 'string') {
    const m = ts.match(/^(\d{4})-(\d{2})(?:-(\d{2}))?/);
    if (m) return m[3] ? `${Number(m[1])}. ${Number(m[2])}. ${Number(m[3])}.` : `${Number(m[1])}. ${Number(m[2])}.`;
    const n = Number(ts);
    if (!isFinite(n)) return ts;
    ts = n;
  }
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}. ${d.getMonth() + 1}. ${d.getDate()}.`;
}
function catColor(id) {
  const i = id == null || id < 0 || id > 10 ? 10 : Number(id);
  const v = getComputedStyle(document.documentElement).getPropertyValue('--c' + i).trim();
  return v || (catOf(i).color || '#5C5C63');
}
const ProjCount = {
  t: 0,
  load() {
    clearTimeout(this.t);
    this.t = setTimeout(async () => {
      if (!S.drives.some(d => d.scanned)) { S.projCount = null; Rail.update(); return; }
      try { const r = await GET('/api/projects', { drive: '*', group: 'time' }); S.projCount = r.total || 0; } catch (e) { /* 다음 기회에 */ }
      Rail.update();
    }, 300);
  },
};
function shellCtx(cleanup) {
  const api = (path, opts = {}) => {
    const method = (opts.method || (opts.body !== undefined ? 'POST' : 'GET')).toUpperCase();
    return api_(method, path, method === 'POST' ? (opts.body || {}) : undefined, opts.query || opts.params);
  };
  const api_ = (m, p, b, q) => apiCall(m, p, b, q);
  return {
    api,
    fmtSize: fmtBytes, fmtAgo: relTime, fmtDate, fmtAbs: absTime, fmtNum, catColor,
    get cats() { return S.cats; },
    drives: () => S.drives,
    select: {
      set(keys, items) { Sel.replace(keys, items); },
      clear() { Sel.clear(); },
      onChange(fn) { Sel.listeners.add(fn); cleanup.push(() => Sel.listeners.delete(fn)); return () => Sel.listeners.delete(fn); },
      get keys() { return [...S.sel.keys()]; },
    },
    openMove(items, mode, anchor) {
      const its = (items || []).filter(selectable);
      if (!its.length) return;
      const m = mode === 'copy' ? 'copy' : 'move';
      const a = anchor || (Tray.shown ? $(m === 'copy' ? '#trCopy' : '#trMove') : { x: Math.max(16, innerWidth / 2 - 210), y: Math.max(16, innerHeight / 2 - 200) });
      MovePop.open(a, its, { mode: m, place: anchor ? 'bottom-end' : 'top' });
    },
    openRecycle(items) { confirmRecycle((items || []).filter(selectable)); },
    openDelete(items) { confirmDelete((items || []).filter(selectable)); },
    reveal,
    toast(msg, opts = {}) { return Toast.show(Object.assign({ kind: 'info', title: msg }, opts)); },
    openSettings(section) { openSettings(section); },
    tip: Tip, icon: ic, h,
  };
}
function ProjectsScreen() {
  const el = h('div', { class: 'proj-screen' });
  const host = h('div', { class: 'proj-host' });
  el.append(host);
  const cleanup = [];
  let inst = null;
  const P = window.Projects;
  if (P && typeof P.mount === 'function') {
    try { inst = P.mount(host, shellCtx(cleanup)) || null; } catch (e) {
      console.error('Projects.mount', e);
      host.append(h('div', { class: 'card' }, h('div', { class: 'empty' }, h('div', { class: 'ill', html: ic('warn') }), h('h3', { text: '프로젝트 화면을 열지 못했어요' }), h('p', { text: String(e && e.message || e) }))));
    }
  } else {
    host.append(h('div', { class: 'card' }, h('div', { class: 'empty' }, h('div', { class: 'ill', html: ic('proj') }), h('h3', { text: '프로젝트 화면을 준비하는 중이에요' }),
      h('p', { text: '프로젝트 모듈을 불러오지 못했어요. 저장 공간 화면은 그대로 쓸 수 있어요.' }))));
  }
  const call = (name, ...a) => { if (inst && typeof inst[name] === 'function') { try { return inst[name](...a); } catch (e) { console.error('Projects.' + name, e); } } return undefined; };
  return {
    el,
    update() { if (S.lastState) call('onState', S.lastState); },
    refresh() { call('refresh'); },
    destroy() { call('unmount'); cleanup.forEach(f => f()); cleanup.length = 0; inst = null; },
    onKey(e) { return call('onKey', e); },
  };
}

/* ═════════════════════════════════════════════ 폴링 */
const Poll = {
  t: 0, inflight: false, fails: 0,
  async tick() {
    clearTimeout(this.t);
    if (this.inflight) { this.t = setTimeout(() => this.tick(), 200); return; }
    this.inflight = true;
    try {
      const st = await GET('/api/state');
      this.fails = 0;
      setOnline(true);
      applyState(st);
    } catch (e) {
      this.fails++;
      if (e.status === 403) setOnline(false, 'forbidden');
      else if (e.code === 'network' || e.status === 0 || e.status >= 500) setOnline(false);
    } finally {
      this.inflight = false;
      const busy = S.busy || S.drives.some(d => isScanning(d.letter)) || S.jobs.some(j => j.status === 'running' || j.status === 'queued') || [...S.my.keys()].some(id => !TERM(S.jobSeen.get(id)));
      const ms = this.fails ? Math.min(5000, 800 * this.fails) : busy ? 400 : 3000;
      this.t = setTimeout(() => this.tick(), ms);
    }
  },
  kick() { clearTimeout(this.t); this.t = setTimeout(() => this.tick(), 60); },
};
function setOnline(on, why) {
  const key = on ? 'on' : (why || 'off');
  if (S.online === key) return;
  S.online = key;
  const el = $('#offline');
  el.classList.toggle('show', !on);
  if (!on) {
    el.innerHTML = why === 'forbidden'
      ? ic('lock') + '<span>앱이 다시 시작됐어요. 여기를 눌러 화면을 새로 불러와 주세요.</span>'
      : ic('offline') + '<span>앱 서버에 연결할 수 없어요. 다시 연결하는 중…</span>';
    el.style.pointerEvents = why === 'forbidden' ? 'auto' : 'none';
    el.style.cursor = why === 'forbidden' ? 'pointer' : '';
    el.onclick = why === 'forbidden' ? () => location.reload() : null;
  }
}
function applyState(st) {
  S.skew = (st.now || Date.now() / 1000) - Date.now() / 1000;
  const prevScans = S.scans;
  const GO = { int: 0, ext: 1, net: 2 };
  const sk = d => (isLetterKey(d.letter) ? '0' + d.letter : d.letter[0] === '~' ? '2' + (d.last_letter || '') + d.letter : '1' + dlabel(d));
  S.drives = (st.drives || []).slice().sort((a, b) => (GO[driveGroup(a)] - GO[driveGroup(b)]) || (sk(a) < sk(b) ? -1 : sk(a) > sk(b) ? 1 : 0));
  S.dmap = {}; S.dmapLower = {};
  S.drives.forEach(d => { S.dmap[d.letter] = d; S.dmapLower[String(d.letter).toLowerCase()] = d; });
  S.update = st.update || null;
  S.previews = st.previews || null;
  S.lastState = st;
  S.scans = st.scans || {};
  S.jobs = st.jobs || [];
  S.busy = !!st.busy;
  S.stateVer++;
  // 스캔 완료 감지
  for (const L in prevScans) {
    const was = prevScans[L], now = S.scans[L];
    const wasAct = was && (was.phase === 'scan' || was.phase === 'finalize');
    const nowAct = now && (now.phase === 'scan' || now.phase === 'finalize');
    if (wasAct && !nowAct) {
      const d = driveOf(L);
      if (now && now.phase === 'canceled') Toast.show({ kind: 'info', title: `${dname(d)} 스캔을 취소했어요` });
      else if (now && now.phase === 'error') Toast.show({ kind: 'err', title: `${dname(d)} 스캔 중 오류가 났어요` });
      else if (d && d.scanned) Toast.show({ kind: 'ok', title: `${dname(d)} 스캔을 마쳤어요`, sub: `파일 ${fmtNum(d.files)}개 · 폴더 ${fmtNum(d.dirs)}개${d.scan_seconds ? ` · ${fmtDur(d.scan_seconds)}` : ''}` });
      Recs.load();
      ProjCount.load();
      if (S.screen && S.screen.refresh && S.route.name === 'list' && (S.route.drive === '*' || S.route.drive === L)) S.screen.refresh('scan');
      if (S.screen && S.screen.refresh && (S.route.name === 'projects' || (S.route.name === 'home' && S.drives.length === 1))) S.screen.refresh('scan');
    }
  }
  // 두 폴링 사이에 시작해서 끝난 짧은 스캔(작은 USB·NAS)은 위 감지를 거치지 않는다 → 스캔 시각이 바뀌면 개수·추천을 다시
  const scanSig = S.drives.map(d => d.letter + '@' + (d.scanned_at || 0)).join('|');
  if (S.scanSig != null && scanSig !== S.scanSig) {
    Recs.load();
    ProjCount.load();
    if (S.screen && S.screen.refresh && S.route.name === 'projects') S.screen.refresh('scan');
  }
  S.scanSig = scanSig;
  Jobs.update(S.jobs);
  Rail.update();
  if (S.screen && S.screen.update) S.screen.update();
  Top.scanBtn();
  Top.updatePill();
  if (S.firstState) { S.firstState = false; Recs.load(); ProjCount.load(); }
}

/* ═════════════════════════════════════════════ 키보드 */
function inField(e) {
  const t = e.target;
  return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
}
window.addEventListener('keydown', e => {
  if (e.defaultPrevented) return;
  const k = e.key;
  if (S.screen && S.screen.onKey && !Sheet.cur && !Pop.stack.length && S.screen.onKey(e)) return;
  if (k === 'F5') { e.preventDefault(); Poll.kick(); Recs.load(); if (S.screen && S.screen.refresh) S.screen.refresh('f5'); else if (S.screen && S.screen.reload) S.screen.reload(); return; }
  if (k === 'Escape') {
    if (Drag.active) { Drag.cancel(); return; }
    if (Pop.stack.length) { Pop.close(Pop.top()); return; }
    if (Sheet.cur) { if (!Sheet.cur.busy) Sheet.close(); return; }
    if (S.sel.size) { Sel.clear(); if (S.screen && S.screen.lv && S.screen.lv.syncCkAll) S.screen.lv.syncCkAll(); return; }
    return;
  }
  if (Sheet.cur) {
    const ae = document.activeElement;
    const free = !ae || ae === document.body || ae === Sheet.cur.primary || !Sheet.cur.el.contains(ae);
    if (k === 'Enter' && !inField(e) && free && Sheet.cur.primary && !Sheet.cur.primary.disabled) {
      e.preventDefault(); Sheet.cur.primary.click();
    }
    return;
  }
  if (inField(e)) return;
  if (Pop.stack.length && (k === 'ArrowDown' || k === 'ArrowUp')) {
    const p = Pop.top();
    if (p.btns && p.btns.length) {
      e.preventDefault();
      const en = p.btns.filter(b => !b.disabled);
      let i = en.indexOf(document.activeElement);
      i = k === 'ArrowDown' ? (i + 1) % en.length : (i - 1 + en.length) % en.length;
      en[i].focus();
    }
    return;
  }
  if (k === '/') { e.preventDefault(); $('#searchIn').focus(); $('#searchIn').select(); return; }
  if ((e.ctrlKey || e.metaKey) && (k === 'a' || k === 'A')) {
    if (S.screen && S.screen.selectAll) { e.preventDefault(); S.screen.selectAll(); }
    return;
  }
  if (k === 'Delete') { if (S.sel.size) { e.preventDefault(); confirmRecycle(Sel.items()); } return; }
  if (k === 'Backspace') {
    e.preventDefault();
    if ((S.screen && S.screen.canUp && S.screen.canUp()) || S.route.name !== 'home') back();
    return;
  }
});
window.addEventListener('mouseup', e => { if (e.button === 3 && S.route.name !== 'home') { e.preventDefault(); back(); } });
document.addEventListener('contextmenu', e => { if (!e.defaultPrevented && !inField(e)) e.preventDefault(); });

/* ═════════════════════════════════════════════ 시작 */
function applyMeta(m) {
  S.meta = m;
  S.cats = (m.categories || []).length ? m.categories : DEFAULT_CATS;
  S.virt = m.virtual || {};
  S.settings = m.settings || {};
  const root = document.documentElement.style;
  S.cats.forEach(c => { if (c.color && /^#[0-9a-f]{3,8}$/i.test(c.color)) root.setProperty('--c' + c.id, c.color); });
  if (S.virt.bin && /^#[0-9a-f]{3,8}$/i.test(S.virt.bin.color || '')) root.setProperty('--cbin', S.virt.bin.color);
  if (S.virt.unscanned && /^#[0-9a-f]{3,8}$/i.test(S.virt.unscanned.color || '')) root.setProperty('--cun', S.virt.unscanned.color);
}
async function boot() {
  Tip.init();
  Tray.init();
  Rail.init();
  Top.init();
  attachScrollbar($('#view'), $('#viewWrap'));
  let tries = 0;
  for (;;) {
    try { applyMeta(await GET('/api/meta')); setOnline(true); break; } catch (e) {
      setOnline(false);
      tries++;
      await sleep(Math.min(4000, 600 * tries));
    }
  }
  try { applyState(await GET('/api/state')); } catch (e) { /* 폴링이 다시 시도 */ }
  mount('fwd');
  Poll.t = setTimeout(() => Poll.tick(), 400);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') Poll.kick(); });
}
/* 창 닫기(X) — 이동·삭제가 진행 중이면 서버(window_closing)가 이걸 부른다. true = 물어봤다(창은 열린 채) */
function askClose(st) {
  st = st || {};
  const jobs = st.jobs || [];
  if (!jobs.length) return false;
  const stay = h('button', { class: 'btn', type: 'button', text: '계속 쓰기' });
  const quit = h('button', { class: 'btn solid-danger', type: 'button', text: '작업을 멈추고 닫기' });
  const grace = Math.max(1, Math.round((st.grace_s || 120) / 60));
  const list = h('div', { class: 'closejobs' }, ...jobs.slice(0, 4).map(j => h('div', { class: 'cj num' }, icEl(j.status === 'running' ? 'spinner' : 'clock', j.status === 'running' ? 'spin' : ''), h('span', { text: j.title || VERB[j.type] || '작업' }))),
    jobs.length > 4 ? h('div', { class: 'cj', text: `외 ${fmtNum(jobs.length - 4)}개` }) : null);
  const body = h('div', {}, list,
    st.deleting
      ? note('warn', 'warn', h('b', { text: '원본을 지우는 중인 항목이 있어요. ' }), `닫으면 그 항목은 마저 지운 뒤(최대 ${grace}분) 창이 닫혀요. 중간에 끊으면 원본과 대상에 나뉘어 남기 때문이에요.`)
      : note('warn', 'info', h('b', { text: '지금 닫으면 진행 중인 작업을 멈춰요. ' }), '끝난 항목은 그대로 두고, 멈춘 항목은 작업 기록에 남아요. 원본은 지우기 전이라 그대로예요.'));
  const sh = Sheet.open({ icon: h('span', { class: 'tile t-lg', html: ic('warn') }), title: '작업이 진행 중이에요', sub: `${fmtNum(jobs.length)}개 작업이 끝나지 않았어요`, body });
  sh.primary = stay;
  sh.setFoot([h('span', { class: 'hint', text: 'Esc 로 계속 쓰기' }), h('span', { class: 'sp' }), quit, stay]);
  stay.addEventListener('click', () => sh.close());
  quit.addEventListener('click', async () => {
    if (quit.disabled) return;
    quit.disabled = true; stay.disabled = true; sh.busy = true;
    try {
      const r = await POST('/api/app/close', {});
      sh.setTitle('닫는 중이에요', r.deleting ? `지우던 항목을 마저 지운 뒤 닫혀요(최대 ${grace}분)` : '진행 중인 작업을 멈추고 있어요');
      sh.setFoot([h('span', { class: 'hint', html: ic('spinner', 'spin') + ' 창이 곧 닫혀요' })]);
    } catch (e) {
      sh.busy = false; quit.disabled = false; stay.disabled = false;
      sh.shake(); sh.body.append(note('danger', 'warn', e.message));
    }
  });
  return true;
}
window.DriveBoard = { fmtBytes, relTime, state: S, askClose };
boot();
})();
