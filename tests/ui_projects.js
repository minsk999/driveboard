#!/usr/bin/env node
/* DriveBoard 프로젝트 화면(ui/projects.js) 종단 시험 — Playwright, 헤드리스 Chromium.
 *
 *   NODE_PATH=/opt/node22/lib/node_modules node tests/ui_projects.js [작업폴더]
 *
 * 가짜 드라이브 3벌(lead · member · 프로젝트 없음)을 만들고 각각 실제 서버(app.py --no-open --fake)를 띄운다.
 * lead 에는 VP9 webm 두 개가 든 프로젝트를 더한다 — 헤드리스 Chromium 은 H.264 를 못 풀어서
 * 호버 재생·시트 재생은 webm 으로 확인하고, ProRes 프로젝트로 프록시 경로(queued → ready)를 확인한다.
 * 화면은 셸 배선과 무관하게 window.Projects.mount 로 직접 붙인다(셸이 붙어 있으면 마지막에 그 경로도 본다).
 * 콘솔 오류·페이지 오류가 하나라도 있으면 exit 1. 스크린샷은 _shots/v2-projects-*.png.
 */
'use strict';
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn, execFileSync } = require('child_process');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const SHOTS = path.join(ROOT, '_shots');
fs.mkdirSync(SHOTS, { recursive: true });
const WORK = path.resolve(process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'db-uiproj-')));
fs.mkdirSync(WORK, { recursive: true });

const log = s => console.log('  · ' + s);
function assert(c, msg) { if (!c) throw new Error('확인 실패: ' + msg); }
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ───────── 가짜 데이터 */
function makeFake(dir, profile) {
  execFileSync('python3', [path.join(ROOT, 'tests', 'make_fake_drives.py'), dir, '--scale', 'demo', '--profile', profile, '--clean'], { stdio: 'ignore' });
}
function ffmpeg(args) { execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...args], { stdio: 'ignore' }); }
function sparse(p, n) { fs.mkdirSync(path.dirname(p), { recursive: true }); const fd = fs.openSync(p, 'w'); fs.ftruncateSync(fd, n); fs.closeSync(fd); }
const WEBM_PROJ = '261026_소담코스_청귤_웹캠페인_KMS';
function addWebmProject(fake) {
  const p = path.join(fake, 'D', 'Projects', WEBM_PROJ);
  fs.mkdirSync(path.join(p, '렌더'), { recursive: true });
  const a = path.join(p, '렌더', 'GD_teaser.webm');
  const b = path.join(p, '렌더', 'GD_cutdown.webm');
  ffmpeg(['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25', '-t', '6', '-c:v', 'libvpx-vp9', '-b:v', '300k', '-deadline', 'realtime', '-cpu-used', '8', '-pix_fmt', 'yuv420p', a]);
  ffmpeg(['-f', 'lavfi', '-i', 'smptehdbars=size=640x360:rate=25', '-t', '5', '-c:v', 'libvpx-vp9', '-b:v', '200k', '-deadline', 'realtime', '-cpu-used', '8', '-pix_fmt', 'yuv420p', b]);
  const now = Date.now() / 1000;
  fs.utimesSync(b, now - 3600, now - 3600);          // teaser 가 가장 최근 = 대표 영상
  fs.utimesSync(a, now - 60, now - 60);
  sparse(path.join(p, 'footage', 'C0001.MP4'), 48 << 20);   // 1MB 넘게(프로젝트 최소 크기)
}
function makeEmptyFake(fake) {
  fs.rmSync(fake, { recursive: true, force: true });
  const C = path.join(fake, 'C');
  fs.mkdirSync(C, { recursive: true });
  fs.writeFileSync(path.join(C, '_drive.json'), JSON.stringify({ label: '로컬 디스크', kind: 'ssd', total: 512 * 2 ** 30, system_bytes: 40 * 2 ** 30, external: false, has_bin: true, network: false, unc: null }));
  fs.mkdirSync(path.join(C, 'Users', 'me', 'Documents'), { recursive: true });
  fs.writeFileSync(path.join(C, 'Users', 'me', 'Documents', '메모.txt'), '안녕하세요\n'.repeat(400));
  // 날짜도 aep 도 없는 이름 — 폴더를 지정해야만 프로젝트가 된다
  sparse(path.join(C, '작업모음', '소담코스_진정크림', 'out', 'final.mp4'), 30 << 20);
  sparse(path.join(C, '작업모음', '한결_브랜드', 'out', 'final.mp4'), 20 << 20);
}

/* ───────── 서버 */
function startServer(fake, home) {
  const port = 9300 + Math.floor(Math.random() * 500);
  const env = Object.assign({}, process.env, { DRIVEBOARD_HOME: home, DRIVEBOARD_NO_UPDATE_CHECK: '1' });
  delete env.DRIVEBOARD_FAKE;
  const proc = spawn('python3', [path.join(ROOT, 'app.py'), '--no-open', '--fake', fake, '--port', String(port)], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '';
  return new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('server did not start: ' + out)), 30000);
    const on = d => {
      out += d;
      const m = out.match(/DriveBoard: (http:\/\/127\.0\.0\.1:\d+\/)/);
      if (m) { clearTimeout(t); res({ url: m[1], proc, log: () => out }); }
    };
    proc.stdout.on('data', on);
    proc.stderr.on('data', d => { out += d; });
    proc.on('exit', c => { clearTimeout(t); rej(new Error('server exited ' + c + ': ' + out)); });
  });
}
async function apiOf(url) {
  const html = await (await fetch(url)).text();
  const tok = (html.match(/name="db-token" content="([^"]+)"/) || [])[1];
  assert(tok, 'db-token 없음');
  const call = async (p, body) => {
    const r = await fetch(url.replace(/\/$/, '') + p, { method: body ? 'POST' : 'GET', headers: Object.assign({ 'X-DB-Token': tok }, body ? { 'Content-Type': 'application/json' } : {}), body: body ? JSON.stringify(body) : undefined });
    return r.json();
  };
  return call;
}
async function scanAll(api, extra) {
  await api('/api/scan', { drives: 'all' });
  if (extra && extra.length) {
    for (let i = 0; i < 100; i++) { const s = await api('/api/state'); if (!s.busy && !Object.values(s.scans || {}).some(x => x.phase === 'scan' || x.phase === 'finalize')) break; await sleep(300); }
    await api('/api/scan', { drives: extra });
  }
  for (let i = 0; i < 300; i++) {
    const s = await api('/api/state');
    const run = Object.values(s.scans || {}).some(x => x.phase === 'scan' || x.phase === 'finalize');
    if (!s.busy && !run && s.drives.filter(d => d.scan || (extra || []).includes(d.letter)).every(d => d.scanned)) return s;
    await sleep(300);
  }
  throw new Error('스캔이 끝나지 않았어요');
}

/* ───────── 화면 붙이기(셸 없이 — 셸 ctx 와 같은 모양) */
async function mountProjects(page) {
  await page.evaluate(async () => {
    if (!window.Projects) {
      await new Promise((res, rej) => { const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = 'projects.css'; l.onload = res; l.onerror = rej; document.head.append(l); });
      await new Promise((res, rej) => { const s = document.createElement('script'); s.src = 'projects.js'; s.onload = res; s.onerror = rej; document.body.append(s); });
    }
    const TOKEN = document.querySelector('meta[name="db-token"]').content;
    const api = async (p, opts = {}) => {
      const o = { method: opts.method || 'GET', headers: { 'X-DB-Token': TOKEN, Accept: 'application/json' }, cache: 'no-store' };
      if (opts.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
      const r = await fetch(p, o);
      const d = await r.json().catch(() => null);
      if (!r.ok || !d || d.ok === false) { const e = new Error((d && d.message) || '요청 실패'); e.code = d && d.error; e.data = d; e.status = r.status; throw e; }
      return d;
    };
    const fmtSize = n => { n = Number(n) || 0; const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (n >= 1000 && i < 4) { n /= 1024; i++; } return (i ? (n >= 100 ? Math.floor(n) : n >= 10 ? Math.floor(n * 10) / 10 : Math.floor(n * 100) / 100) : n) + u[i]; };
    const T = window.__T = { calls: [], toasts: [], sel: [], lastActions: null };
    let drives = [];
    const listeners = new Set();
    const ctx = {
      api, fmtSize,
      fmtAgo: ts => { const s = Date.now() / 1000 - ts; return s < 86400 ? '오늘' : Math.floor(s / 86400) + '일 전'; },
      fmtDate: ts => new Date(ts * 1000).toLocaleDateString('ko-KR'), catColor: i => `var(--c${i})`, cats: [],
      drives: () => drives,
      select: {
        set(keys) { T.sel = keys.slice(); },
        clear() { T.sel = []; listeners.forEach(f => f([])); },
        onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
      },
      openMove(items, mode) { T.calls.push(['openMove', mode, items.map(i => i.path), items.map(i => i.key)]); },
      openRecycle(items) { T.calls.push(['openRecycle', items.map(i => i.path)]); },
      openDelete(items) { T.calls.push(['openDelete', items.map(i => i.path)]); },
      reveal(p) { T.calls.push(['reveal', p]); },
      toast(msg, o = {}) {
        T.toasts.push([msg, o.kind || '']);
        T.lastActions = o.actions || null;
        const box = document.getElementById('toasts');
        const el = document.createElement('div');
        el.className = 'toast';
        el.innerHTML = '<div class="tb"><div class="tt"></div><div class="ts"></div></div><div class="ta"></div>';
        el.querySelector('.tt').textContent = msg;
        el.querySelector('.ts').textContent = o.sub || '';
        for (const a of o.actions || []) { const b = document.createElement('button'); b.className = 'btn sm lime'; b.textContent = a.label; b.onclick = () => { el.remove(); a.onClick(); }; el.querySelector('.ta').append(b); }
        box.append(el);
        setTimeout(() => el.remove(), 4000);
        return { close() { el.remove(); } };
      },
      openSettings(s) { T.calls.push(['openSettings', s]); },
    };
    const st = await api('/api/state');
    drives = st.drives;
    const meta = await api('/api/meta');
    ctx.cats = meta.categories;
    for (const c of meta.categories) document.documentElement.style.setProperty('--c' + c.id, c.color);
    const view = document.getElementById('view');
    [...view.children].forEach(c => { c.style.display = 'none'; });
    const host = document.createElement('div');
    host.className = 'screen';
    view.append(host);
    const inst = window.__P = window.Projects.mount(host, ctx);
    window.__clearSel = () => ctx.select.clear();
    // 셸처럼 /api/state 를 넘긴다
    window.__poll = setInterval(async () => { try { const s = await api('/api/state'); drives = s.drives; inst.onState(s); } catch (e) { /* 무시 */ } }, 900);
  });
  await page.waitForSelector('.pj .pc, .pj .pj-empty', { timeout: 20000 });
}

/* ───────── 공통 페이지 */
async function openPage(browser, url, vp, errors, tag) {
  const ctx = await browser.newContext({ viewport: vp, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on('console', m => { if (m.type() === 'error') errors.push(`[${tag}] console: ${m.text()}`); });
  page.on('pageerror', e => errors.push(`[${tag}] pageerror: ${(e && e.stack) || e}`));
  page.on('response', r => {
    const u = r.url();
    if (r.status() >= 400 && !u.endsWith('favicon.ico') && !u.includes('/media/')) errors.push(`[${tag}] http ${r.status()}: ${u}`);
  });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  // 셸이 첫 실행 화면 등을 그릴 시간
  await page.waitForTimeout(600);
  return page;
}
const shotter = page => async (name, settle = 500, keepMouse = false) => {
  if (!keepMouse) await page.mouse.move(2, 2).catch(() => {});
  await page.waitForTimeout(Math.max(settle, keepMouse ? 0 : 120));
  await page.screenshot({ path: path.join(SHOTS, `v2-projects-${name}.png`) });
  log(`shot v2-projects-${name}.png`);
};
const allProjects = d => d.groups.flatMap(g => g.projects);
const cardSel = key => `.pc[data-k="${key}"]`;

/* ═════════════════════════════════════════════ 1. 팀장 PC(lead) */
async function leadSuite(browser, errors) {
  const FAKE = path.join(WORK, 'lead'), HOME = path.join(WORK, 'home-lead');
  makeFake(FAKE, 'lead');
  addWebmProject(FAKE);
  fs.rmSync(HOME, { recursive: true, force: true });
  const srv = await startServer(FAKE, HOME);
  log('lead server ' + srv.url);
  try {
    const api = await apiOf(srv.url);
    await scanAll(api, ['J', 'K']);
    const pc = await api('/api/projects?group=client');
    const ps = allProjects(pc);
    log(`projects: ${pc.total}, clients ${pc.clients.length}`);
    assert(pc.total > 40, 'lead 프로젝트 수');
    const webm = ps.find(p => p.name === WEBM_PROJ);
    assert(webm && webm.preview && webm.preview.ext === 'webm', 'webm 프로젝트 감지');
    const prores = ps.find(p => p.connected && p.preview && /ProRes\.mov$/.test(p.preview.name) && p.preview.size < 8e6 && p.drive !== 'K');
    assert(prores, 'ProRes 실제 클립 프로젝트');

    // ── ffmpeg 가 없을 때: 브라우저가 <video> 1초 지점을 캔버스로 떠서 포스터를 만든다
    //    (서버가 아직 포스터를 만들기 전 — 목록의 poster_url 이 비어 있을 때 먼저 본다)
    {
      const p2 = await openPage(browser, srv.url, { width: 1360, height: 860 }, errors, 'noffmpeg');
      await p2.route('**/api/media/poster', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, status: 'no_ffmpeg', playable: 'direct' }) }));
      await mountProjects(p2);
      await p2.fill('.pj-q input', '청귤 웹캠페인');
      await p2.waitForFunction(k => { const c = document.querySelectorAll('.pj .pc'); return c.length === 1 && c[0].dataset.k === k; }, webm.key, { timeout: 8000 });
      await p2.waitForFunction(k => { const i = document.querySelector(`.pc[data-k="${k}"] .po-img`); return i && i.classList.contains('on') && (i.getAttribute('src') || '').startsWith('data:image/jpeg'); }, webm.key, { timeout: 15000 });
      await p2.screenshot({ path: path.join(SHOTS, 'v2-projects-client-poster.png') });
      log('no-ffmpeg client poster ok');
      await p2.close();
    }

    const page = await openPage(browser, srv.url, { width: 1360, height: 860 }, errors, 'lead');
    const shot = shotter(page);
    await mountProjects(page);

    // ── 광고주별
    await page.waitForFunction(n => document.querySelectorAll('.pj .pc').length === n, pc.total, { timeout: 15000 });
    assert(await page.locator('.pj-sec').count() === pc.groups.length, '광고주 섹션 수');
    const firstTitle = await page.locator('.pj-sec .pj-sh .nm b').first().textContent();
    assert(firstTitle === pc.groups[0].title, `첫 섹션 = 용량 큰 광고주 (${firstTitle})`);
    assert((await page.locator('.pj-head .sub').textContent()).includes(`${pc.total}개`), '머리 합계');
    await page.waitForFunction(() => document.querySelectorAll('.po-img.on').length >= 4, null, { timeout: 30000 });
    log('posters loaded: ' + await page.locator('.po-img.on').count());
    await shot('lead-client', 900);

    // 접기·펴기(높이 애니메이션)
    const sec0 = page.locator('.pj-sec').first();
    await sec0.locator('.pj-sh').click();
    await page.waitForTimeout(600);
    const hCol = await sec0.locator('.pj-sb').evaluate(e => e.getBoundingClientRect().height);
    assert(hCol < 2, '접으면 높이 0: ' + hCol);
    await sec0.locator('.pj-sh').click();
    await page.waitForTimeout(600);
    assert(await sec0.locator('.pj-sb').evaluate(e => e.getBoundingClientRect().height) > 150, '다시 펼침');
    // 가로 줄 화살표
    const rw = page.locator('.pj-rw').first();
    await rw.hover();
    assert(await rw.evaluate(e => e.classList.contains('fr')), '오른쪽 가장자리 페이드');
    await rw.locator('.pj-arr.r').click();
    await page.waitForFunction(() => document.querySelector('.pj-row').scrollLeft > 100, null, { timeout: 4000 });
    await page.waitForTimeout(500);
    assert(await rw.evaluate(e => e.classList.contains('fl')), '왼쪽 가장자리 페이드');
    await rw.locator('.pj-arr.l').click();
    log('collapse + row arrows ok');

    // ── 호버 재생(webm) · 스크럽
    const wc = page.locator(cardSel(webm.key));
    await wc.scrollIntoViewIfNeeded();
    await page.mouse.move(2, 2);
    await page.waitForTimeout(300);
    await wc.evaluate(el => {
      const w = window.__hv = { t0: 0, t1: 0 };
      el.querySelector('.pc-po').addEventListener('pointerenter', () => { if (!w.t0) w.t0 = performance.now(); });
      new MutationObserver(() => { if (!w.t1 && el.querySelector('video')) w.t1 = performance.now(); }).observe(el, { subtree: true, childList: true });
    });
    const box = await wc.locator('.pc-po').boundingBox();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await page.waitForFunction(k => { const c = document.querySelector(`.pc[data-k="${k}"]`); const v = c && c.querySelector('video'); return c.classList.contains('playing') && v && v.currentTime > 0.05; }, webm.key, { timeout: 8000 });
    const hv = await page.evaluate(() => window.__hv);
    assert(hv.t0 && hv.t1 && hv.t1 - hv.t0 >= 240, `호버 0.25초 뒤에 재생 시작 ${JSON.stringify(hv)}`);
    const t1 = await wc.evaluate(e => e.querySelector('video').currentTime);
    await page.waitForTimeout(700);
    const t2 = await wc.evaluate(e => e.querySelector('video').currentTime);
    assert(t2 > t1 + 0.2, `호버 재생 진행 ${t1} → ${t2}`);
    assert(await page.evaluate(() => document.querySelectorAll('.pj video').length) === 1, '재생 중인 카드는 하나');
    assert(await wc.evaluate(e => e.querySelector('video').muted), '음소거 재생');
    await shot('hover', 0, true);
    // 스크럽: 가로로 움직이면 커서 위치로
    for (let i = 0; i <= 8; i++) { await page.mouse.move(box.x + box.width * (0.5 + i * 0.04), box.y + box.height * 0.5); await page.waitForTimeout(30); }
    await page.waitForTimeout(300);
    const sc = await wc.evaluate(e => ({ scrub: e.classList.contains('scrub'), t: e.querySelector('video').currentTime, d: e.querySelector('video').duration }));
    assert(sc.scrub && Math.abs(sc.t / sc.d - 0.82) < 0.12, `스크럽 위치 ${JSON.stringify(sc)}`);
    await shot('scrub', 0, true);
    await page.mouse.move(2, 2);
    await page.waitForTimeout(600);
    assert(!(await wc.evaluate(e => e.classList.contains('playing'))) && await page.evaluate(() => document.querySelectorAll('.pj video').length) === 0, '떠나면 정지·포스터로');
    log(`hover-play ok (${t1.toFixed(2)} → ${t2.toFixed(2)}s), scrub ok`);

    // ── 프록시 경로(ProRes): queued → ready
    const statuses = [];
    const onResp = async r => {
      if (!r.url().endsWith('/api/media/proxy')) return;
      try { const b = JSON.parse(r.request().postData() || '{}'); if (b.path === prores.preview.path) statuses.push((await r.json()).status); } catch (e) { /* 무시 */ }
    };
    page.on('response', onResp);
    const prc = page.locator(cardSel(prores.key));
    await prc.scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    const pbox = await prc.locator('.pc-po').boundingBox();
    await page.mouse.move(pbox.x + pbox.width / 2, pbox.y + pbox.height / 2);
    await page.waitForFunction(k => document.querySelector(`.pc[data-k="${k}"]`).classList.contains('queued'), prores.key, { timeout: 8000, polling: 'raf' });
    await shot('proxy-queued', 0, true);
    for (let i = 0; i < 120 && !statuses.includes('ready'); i++) await page.waitForTimeout(250);
    page.off('response', onResp);
    log('proxy statuses: ' + statuses.join(' → '));
    assert(statuses[0] === 'queued' && statuses[statuses.length - 1] === 'ready', '프록시 queued → ready');
    await page.waitForFunction(k => !document.querySelector(`.pc[data-k="${k}"]`).classList.contains('queued'), prores.key, { timeout: 5000 });
    await page.mouse.move(2, 2);
    await page.waitForTimeout(400);

    // ── 시간순
    await page.click('.pj-tabs button[data-id="time"]');
    const pt = await api('/api/projects?group=time');
    await page.waitForFunction(n => document.querySelectorAll('.pj-mh').length === n, pt.groups.length, { timeout: 10000 });
    assert((await page.locator('.pj-mh h3').first().textContent()) === pt.groups[0].title, '첫 구분선 = 최신 달');
    assert(/^\d{4}년 \d{1,2}월$/.test(pt.groups[0].title), '구분선 형식');
    await shot('lead-time', 700);
    await page.evaluate(() => { document.getElementById('view').scrollTop = 1500; });
    await page.waitForTimeout(500);
    const sticky = await page.evaluate(() => {
      const bar = document.querySelector('.pj-bar').getBoundingClientRect();
      const st = [...document.querySelectorAll('.pj-mh')].find(m => m.classList.contains('stuck'));
      return st ? { d: Math.abs(st.getBoundingClientRect().top - bar.bottom), stuckBar: document.querySelector('.pj-bar').classList.contains('stuck') } : null;
    });
    assert(sticky && sticky.d < 2 && sticky.stuckBar, '스티키 월 구분선 ' + JSON.stringify(sticky));
    await shot('lead-time-sticky', 300);
    log('time view ok');

    // ── 연·월별
    await page.evaluate(() => { document.getElementById('view').scrollTop = 0; });
    await page.click('.pj-tabs button[data-id="month"]');
    await page.waitForSelector('.pj-chart .pj-col');
    const pm = await api('/api/projects?group=month');
    assert(await page.locator('.pj-years .chip').count() === pm.years.length, '연도 칩 수');
    assert(await page.locator('.pj-col').count() === 12, '12칸 막대');
    assert(await page.locator('.pj-mon').count() === pm.groups.length, '월 섹션 수');
    await shot('lead-month', 900);
    const target = pm.groups[Math.min(2, pm.groups.length - 1)].key;
    const tip = await page.locator(`.pj-col[data-m="${target}"]`).getAttribute('data-tip');
    assert(/프로젝트 \d+개/.test(tip), '막대 툴팁 ' + tip);
    await page.click(`.pj-col[data-m="${target}"]`);
    await page.waitForTimeout(1100);
    const pos = await page.evaluate(k => {
      const bar = document.querySelector('.pj-bar').getBoundingClientRect();
      const m = document.getElementById('pjm-' + k).getBoundingClientRect();
      return { d: m.top - bar.bottom, cur: (document.querySelector('.pj-col.cur') || {}).dataset };
    }, target);
    assert(Math.abs(pos.d) < 30 && pos.cur && pos.cur.m === target, `막대 누르면 그 달로 ${JSON.stringify(pos)}`);
    await shot('lead-month-scrolled', 200);
    // 다른 해
    if (pm.years.length > 1) {
      await page.evaluate(() => { document.getElementById('view').scrollTop = 0; });
      await page.locator('.pj-years .chip').nth(1).click();
      await page.waitForFunction(y => { const c = document.querySelector('.pj-years .chip.on'); return c && c.textContent.startsWith(y + '년'); }, pm.years[1].year, { timeout: 8000 });
      const n2 = pm.years[1].months.reduce((a, m) => a + m.count, 0);
      await page.waitForFunction(n => document.querySelectorAll('.pj .pc').length === n, n2, { timeout: 8000 });
    }
    log('month view ok');

    // ── 필터 · 검색 · 정렬
    await page.click('.pj-tabs button[data-id="client"]');
    await page.waitForSelector('.pj-sec');
    await page.click('.pj-bar .dd[aria-label="드라이브"]');
    await page.click('.pop .mi:has-text("(D:)")');
    const pd = await api('/api/projects?group=client&drive=D');
    await page.waitForFunction(n => document.querySelectorAll('.pj .pc').length === n, pd.total, { timeout: 8000 });
    assert(!(await page.locator('.pj-reset').isHidden()), '초기화 버튼');
    await page.fill('.pj-q input', '청귤');
    await page.waitForFunction(k => { const cs = document.querySelectorAll('.pj .pc'); return cs.length >= 1 && [...cs].some(c => c.dataset.k === k); }, webm.key, { timeout: 8000 });
    await page.click('.pj-reset');
    await page.waitForFunction(n => document.querySelectorAll('.pj .pc').length === n, pc.total, { timeout: 8000 });
    await page.click('.pj-bar .dd[aria-label="정렬"]');
    await page.click('.pop .mi:has-text("이름순")');
    await page.waitForTimeout(700);
    await page.click('.pj-bar .dd[aria-label="정렬"]');
    await page.click('.pop .mi:has-text("최신순")');
    await page.waitForTimeout(500);
    log('filters ok');

    // ── 선택(Ctrl+클릭 · 체크) → 공유 선택
    const k0 = ps.find(p => p.connected && p.key !== webm.key).key;
    await page.locator(cardSel(k0)).scrollIntoViewIfNeeded();
    await page.locator(cardSel(k0)).click({ modifiers: ['Control'], position: { x: 40, y: 40 } });
    let sel = await page.evaluate(() => window.__T.sel);
    assert(sel.length === 1 && /:dir:\d+$/.test(sel[0]), 'Ctrl+클릭 선택 ' + JSON.stringify(sel));
    assert(await page.locator(cardSel(k0)).evaluate(e => e.classList.contains('sel')), '선택 표시');
    await page.locator(cardSel(webm.key)).scrollIntoViewIfNeeded();
    await page.locator(cardSel(webm.key)).hover();
    await page.locator(cardSel(webm.key) + ' .pc-ck').click();
    sel = await page.evaluate(() => window.__T.sel);
    assert(sel.length === 2, '체크로 두 번째 선택');
    await shot('selection', 300);
    await page.evaluate(() => window.__clearSel());   // 셸 트레이의 [선택 해제] 와 같은 경로
    assert(await page.locator('.pc.sel').count() === 0, '셸 쪽 선택 해제가 카드에 반영');
    log('selection ok');

    // ── 프로젝트 시트: 재생·키보드·영상 바꾸기
    await page.locator(cardSel(webm.key) + ' .pc-t').click();
    await page.waitForSelector('.pj-side.show');
    await page.waitForFunction(() => document.querySelectorAll('.ps-v').length >= 2 && !document.querySelector('.ps-v .ps-sk'), null, { timeout: 10000 });
    assert(await page.locator('.ps-v').count() === 3, '영상 목록 3개(webm 2 + 촬영 1)');
    await page.keyboard.press(' ');
    await page.waitForFunction(() => { const v = document.querySelector('.pp video.on'); return v && !v.paused && v.currentTime > 0.3; }, null, { timeout: 8000 });
    const before = await page.evaluate(() => document.querySelector('.pp video.on').currentTime);
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(150);
    const after = await page.evaluate(() => document.querySelector('.pp video.on').currentTime);
    assert(after > before + 2, `→ 로 5초 앞으로 ${before} → ${after}`);
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(150);
    const back = await page.evaluate(() => document.querySelector('.pp video.on').currentTime);
    assert(back < after - 2, `← 로 뒤로 ${after} → ${back}`);
    const m0 = await page.evaluate(() => document.querySelector('.pp video.on').muted);
    await page.keyboard.press('m');
    assert(await page.evaluate(() => document.querySelector('.pp video.on').muted) === !m0, 'M 으로 음소거');
    await page.keyboard.press('m');
    await page.mouse.move(800, 300);
    await shot('sheet', 400, true);
    // 스크러버 호버 시간
    const skb = await page.locator('.pp-sk').boundingBox();
    await page.mouse.move(skb.x + skb.width * 0.5, skb.y + skb.height / 2);
    await page.waitForTimeout(200);
    assert(/^\d+:\d\d$/.test(await page.locator('.pp-sk .bub').textContent()), '스크러버 호버 시간');
    await page.keyboard.press(' ');
    await page.waitForTimeout(200);
    assert(await page.evaluate(() => document.querySelector('.pp video.on').paused), 'Space 로 멈춤');
    // 목록에서 두 번째 webm 으로 바꾸기(크로스페이드)
    const src0 = await page.evaluate(() => document.querySelector('.pp video.on').getAttribute('src'));
    await page.locator('.ps-v', { hasText: 'GD_cutdown.webm' }).click();
    await page.waitForFunction(s => { const v = document.querySelector('.pp video.on'); return v && v.getAttribute('src') !== s && !v.paused && v.currentTime > 0.1; }, src0, { timeout: 8000 });
    assert(await page.locator('.ps-v.on .n').textContent() === 'GD_cutdown.webm', '선택한 영상 표시');
    assert(await page.locator('.pp video').count() === 2, '크로스페이드용 두 겹');
    await page.keyboard.press('ArrowUp');
    await page.waitForFunction(() => document.querySelector('.ps-v.on .n').textContent === 'GD_teaser.webm', null, { timeout: 4000 });
    await page.keyboard.press('f');
    await page.waitForTimeout(400);
    const fs1 = await page.evaluate(() => !!document.fullscreenElement && document.fullscreenElement.classList.contains('pp'));
    if (fs1) { await page.keyboard.press('f'); await page.waitForTimeout(300); }
    log('fullscreen toggle: ' + (fs1 ? 'entered' : 'not available headless'));
    assert(await page.locator('.ps-dl').textContent().then(t => t.includes('폴더 이름') && t.includes('KMS')), '메타데이터(날짜 출처·작업자)');
    await page.click('.ps-dl .path .ibtn');
    let calls = await page.evaluate(() => window.__T.calls);
    assert(calls.some(c => c[0] === 'reveal' && c[1] === webm.path), '경로 → 탐색기');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('.pj-side'), null, { timeout: 3000 });
    // 시트의 동작 → ctx
    await page.locator(cardSel(webm.key) + ' .pc-t').click();
    await page.waitForSelector('.pj-side.show');
    await page.click('.pj-side .sd-f .btn:has-text("복사")');
    await page.waitForFunction(() => !document.querySelector('.pj-side'), null, { timeout: 3000 });
    await page.locator(cardSel(webm.key) + ' .pc-t').click();
    await page.waitForSelector('.pj-side.show');
    await page.click('.pj-side .sd-f .btn:has-text("다른 위치로 보관")');
    await page.waitForTimeout(400);
    await page.locator(cardSel(webm.key) + ' .pc-t').click();
    await page.waitForSelector('.pj-side.show');
    await page.click('.pj-side .sd-f .btn:has-text("휴지통")');
    await page.waitForTimeout(400);
    calls = await page.evaluate(() => window.__T.calls);
    assert(calls.some(c => c[0] === 'openMove' && c[1] === 'copy' && c[2][0] === webm.path && c[3][0] === `D:dir:${webm.did}`), '복사 → ctx.openMove(copy)');
    assert(calls.some(c => c[0] === 'openMove' && c[1] === 'move'), '보관 → ctx.openMove(move)');
    assert(calls.some(c => c[0] === 'openRecycle'), '휴지통 → ctx.openRecycle');
    log('sheet ok');

    // ── 프로젝트가 아니에요 → 되돌리기
    const victim = ps.find(p => p.connected && p.client === pc.groups[1].title);
    await page.locator(cardSel(victim.key)).scrollIntoViewIfNeeded();
    await page.locator(cardSel(victim.key) + ' .pc-t').click();
    await page.waitForSelector('.pj-side.show');
    await page.click('.pj-side .sd-f .btn:has-text("프로젝트가 아니에요")');
    await page.waitForFunction(k => !document.querySelector(`.pc[data-k="${k}"]`), victim.key, { timeout: 8000 });
    let s1 = (await api('/api/settings')).settings;
    assert(s1.project_excluded.length === 1, 'project_excluded 저장');
    await page.click('#toasts .toast .btn:has-text("되돌리기")');
    await page.waitForFunction(k => !!document.querySelector(`.pc[data-k="${k}"]`), victim.key, { timeout: 8000 });
    s1 = (await api('/api/settings')).settings;
    assert(s1.project_excluded.length === 0, '되돌리기');
    log('not_project + undo ok');

    // ── 광고주 정리: 합치기 → 다시 묶임
    const cl = pc.clients.filter(c => c.name !== '미분류');
    const A = cl[cl.length - 2], B = cl[cl.length - 1];     // 작은 둘
    await page.click('.pj-head .btn:has-text("광고주 정리")');
    await page.waitForSelector('.pj-side.og .og-row');
    await page.locator(`.og-row[data-name="${A.name}"]`).click();
    await page.locator(`.og-row[data-name="${B.name}"]`).click();
    await page.waitForSelector('.og-merge');
    await shot('organizer', 400);
    const keep = A.bytes >= B.bytes ? A : B, gone = keep === A ? B : A;
    assert((await page.locator('.og-merge .tabs button.on').textContent()) === keep.name, '기본으로 큰 쪽 이름을 남김');
    await page.click('.og-merge .btn:has-text("합치기")');
    await page.waitForFunction(n => !document.querySelector(`.og-row[data-name="${n}"]`), gone.name, { timeout: 8000 })
      .catch(async e => { console.log('DEBUG', JSON.stringify(await page.evaluate(() => window.__T.toasts)), JSON.stringify((await api('/api/settings')).settings.client_aliases), await page.evaluate(() => [...document.querySelectorAll('.og-row')].map(r => r.dataset.name).join(','))); throw e; });
    const s2 = (await api('/api/settings')).settings;
    assert(s2.client_aliases[gone.name] === keep.name, '별칭 저장 ' + JSON.stringify(s2.client_aliases));
    assert((await page.locator(`.og-row[data-name="${keep.name}"] .n span`).first().textContent()) === `${A.count + B.count}개`, '합친 개수');
    // 이름 바꾸기
    const renamed = keep.name + ' 그룹';
    await page.locator(`.og-row[data-name="${keep.name}"]`).hover();
    await page.locator(`.og-row[data-name="${keep.name}"] .ibtn`).click();
    await page.fill('.og-row.edit input', renamed);
    await page.keyboard.press('Enter');
    await page.waitForSelector(`.og-row[data-name="${renamed}"]`, { timeout: 8000 });
    // 작업자 이름
    await page.click('.og-tabs button:has-text("작업자")');
    await page.waitForSelector('.og-w');
    await page.fill('.og-w:has(.ini:text-is("KMS")) input', '테스트 작업자');
    await page.click('.pj-side .sd-f .btn:has-text("저장")');
    await page.waitForFunction(() => [...document.querySelectorAll('.pj .pc-m')].some(e => e.textContent.includes('테스트 작업자')), null, { timeout: 8000 })
      .catch(async e => { console.log('DEBUG', JSON.stringify(await page.evaluate(() => window.__T.toasts)), JSON.stringify((await api('/api/settings')).settings.workers), await page.evaluate(() => document.querySelector('.pj-side .sd-f').outerHTML.slice(0, 400))); throw e; });
    const s3 = (await api('/api/settings')).settings;
    assert(s3.workers.KMS === '테스트 작업자', '작업자 이름 저장');
    await shot('organizer-workers', 300);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('.pj-side'), null, { timeout: 3000 });
    // 다시 묶임: 합친 광고주 하나의 섹션, 사라진 이름은 없음
    await page.waitForFunction(([r, g]) => {
      const t = [...document.querySelectorAll('.pj-sec .pj-sh .nm b')].map(e => e.textContent);
      return t.includes(r) && !t.includes(g);
    }, [renamed, gone.name], { timeout: 10000 });
    const cnt = await page.locator(`.pj-sec:has(.pj-sh .nm b:text-is("${renamed}")) .pc`).count();
    assert(cnt === A.count + B.count, `다시 묶인 섹션 카드 ${cnt}`);
    await page.locator(`.pj-sec:has(.pj-sh .nm b:text-is("${renamed}"))`).scrollIntoViewIfNeeded();
    await shot('lead-merged', 600);
    log(`organizer ok (${gone.name} → ${keep.name} → ${renamed})`);

    // ── 좁은 창(1100×720)
    await page.setViewportSize({ width: 1100, height: 720 });
    await page.evaluate(() => { document.getElementById('view').scrollTop = 0; });
    await shot('lead-client-1100', 900);
    await page.locator(cardSel(webm.key) + ' .pc-t').scrollIntoViewIfNeeded();
    await page.locator(cardSel(webm.key) + ' .pc-t').click();
    await page.waitForSelector('.pj-side.show');
    await page.waitForFunction(() => document.querySelectorAll('.ps-v').length >= 2, null, { timeout: 10000 });
    await shot('sheet-1100', 700);
    await page.keyboard.press('Escape');
    await page.click('.pj-tabs button[data-id="month"]');
    await page.waitForSelector('.pj-chart .pj-col');
    await shot('lead-month-1100', 900);
    await page.close();

    // ── 셸이 붙인 경로(셸 배선이 있으면)
    await shellSmoke(browser, srv.url, errors);
  } finally {
    srv.proc.kill();
  }
}
async function shellSmoke(browser, url, errors) {
  const shellErr = [];
  const page = await openPage(browser, url, { width: 1360, height: 860 }, shellErr, 'shell');
  const nav = await page.$('[data-nav="projects"]');
  if (!nav) { log('셸 내비 없음 — 셸 경로 확인은 건너뜀'); await page.close(); return; }
  await nav.click();
  await page.waitForSelector('.pj .pc', { timeout: 20000 });
  await page.waitForTimeout(800);
  const k = await page.locator('.pj .pc').first().getAttribute('data-k');
  await page.locator(cardSel(k)).click({ modifiers: ['Control'], position: { x: 40, y: 40 } });
  await page.waitForTimeout(700);
  const tray = await page.evaluate(() => { const t = document.getElementById('tray'); return !!(t && t.classList.contains('show')); });
  await page.screenshot({ path: path.join(SHOTS, 'v2-projects-shell.png') });
  log(`shell: mounted, tray ${tray ? 'shown' : 'not shown'}`);
  if (!tray) shellErr.push('[shell] 셸 트레이가 프로젝트 선택에 반응하지 않음');
  await page.close();
  // 셸 쪽 오류는 따로 알린다(셸 파일은 이 시험의 대상이 아니다)
  for (const e of shellErr) console.log('  ! ' + e);
  errors.shell = shellErr;
}

/* ═════════════════════════════════════════════ 2. 팀원 PC(member) */
async function memberSuite(browser, errors) {
  const FAKE = path.join(WORK, 'member'), HOME = path.join(WORK, 'home-member');
  makeFake(FAKE, 'member');
  fs.rmSync(HOME, { recursive: true, force: true });
  const srv = await startServer(FAKE, HOME);
  log('member server ' + srv.url);
  try {
    const api = await apiOf(srv.url);
    const st = await scanAll(api);
    assert(st.drives.length <= 2, 'member 드라이브 1~2개');
    const pc = await api('/api/projects?group=client');
    assert(pc.total > 0, 'member 프로젝트 있음');
    const page = await openPage(browser, srv.url, { width: 1100, height: 720 }, errors, 'member');
    const shot = shotter(page);
    await mountProjects(page);
    await page.waitForFunction(n => document.querySelectorAll('.pj .pc').length === n, pc.total, { timeout: 15000 });
    await page.waitForFunction(() => document.querySelectorAll('.po-img.on, .pc.pvfail, .pc.nopv').length >= Math.min(3, document.querySelectorAll('.pc').length), null, { timeout: 30000 });
    await shot('member-client', 800);
    for (const g of ['time', 'month']) {
      await page.click(`.pj-tabs button[data-id="${g}"]`);
      const d = await api('/api/projects?group=' + g);
      await page.waitForFunction(([n, m]) => document.querySelectorAll('.pj .pc').length === n && document.querySelectorAll('.pj-mh').length === m, [d.total, d.groups.length], { timeout: 10000 });
      await shot('member-' + g, 800);
    }
    await page.setViewportSize({ width: 1360, height: 860 });
    await page.click('.pj-tabs button[data-id="client"]');
    await page.waitForSelector('.pj-sec');
    await shot('member-client-1360', 900);
    log(`member ok (${pc.total} projects, ${st.drives.length} drives)`);
    await page.close();
  } finally {
    srv.proc.kill();
  }
}

/* ═════════════════════════════════════════════ 3. 프로젝트 없음 → 빈 상태 → 폴더 지정 */
async function emptySuite(browser, errors) {
  const FAKE = path.join(WORK, 'empty'), HOME = path.join(WORK, 'home-empty');
  makeEmptyFake(FAKE);
  fs.rmSync(HOME, { recursive: true, force: true });
  const srv = await startServer(FAKE, HOME);
  log('empty server ' + srv.url);
  try {
    const api = await apiOf(srv.url);
    await scanAll(api);
    const pc = await api('/api/projects');
    assert(pc.total === 0 && pc.scanned >= 1, '프로젝트 없음');
    const page = await openPage(browser, srv.url, { width: 1360, height: 860 }, errors, 'empty');
    const shot = shotter(page);
    await mountProjects(page);
    await page.waitForSelector('.pj-empty h3');
    assert((await page.locator('.pj-empty h3').textContent()) === '프로젝트 폴더를 찾지 못했어요', '빈 상태 제목');
    assert((await page.locator('.pj-empty p').first().textContent()).includes('프로젝트를 모아 둔 폴더를 지정해 주세요'), '빈 상태 안내');
    await shot('empty', 700);
    await page.click('.pj-empty .btn:has-text("폴더 지정")');
    await page.waitForSelector('.pj-empty .addrow:not([hidden]) input', { timeout: 5000 });   // 창 없는 실행 → 경로 입력
    await page.fill('.pj-empty .addrow input', path.join(FAKE, 'C', '작업모음'));
    await page.click('.pj-empty .addrow .btn:has-text("추가")');
    await page.waitForFunction(() => document.querySelectorAll('.pj .pc').length === 2, null, { timeout: 10000 });
    const s = (await api('/api/settings')).settings;
    assert(s.project_roots.length === 1, 'project_roots 저장');
    await shot('empty-after-root', 900);
    log('empty state + 폴더 지정 ok');
    await page.close();
  } finally {
    srv.proc.kill();
  }
}

(async () => {
  console.log('DriveBoard 프로젝트 화면 시험 — ' + WORK);
  const browser = await chromium.launch();
  const errors = [];
  let failed = null;
  try {
    await leadSuite(browser, errors);
    await memberSuite(browser, errors);
    await emptySuite(browser, errors);
  } catch (e) {
    failed = e;
  } finally {
    await browser.close();
  }
  if (errors.length) { console.log('\n콘솔·페이지 오류:'); errors.forEach(e => console.log('  ✗ ' + e)); }
  if (failed) { console.log('\n실패: ' + (failed.stack || failed)); process.exit(1); }
  if (errors.length) process.exit(1);
  console.log('\n통과 — 콘솔 오류 0');
})();
