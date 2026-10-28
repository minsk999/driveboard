#!/usr/bin/env node
/* DriveBoard v2 종단 시험(Playwright, 헤드리스 Chromium, 1360x860) — 실제 서버 + 실제 셸 내비게이션.
 *
 *   NODE_PATH=/opt/node22/lib/node_modules node tests/e2e.js [작업폴더]
 *
 * 팀장 PC(lead: 내장 7 + 외장 J + USB K + NAS) 구성에서, 셸(ui/app.js)과 프로젝트 화면(ui/projects.js)을
 * 시험 하네스 없이 실제 「프로젝트」 내비로 붙여 SPEC-2 흐름을 끝까지 눌러 본다.
 *   모두 스캔 → 외장·USB 스캔 → NAS 추가(장치 이름 → 공유 찾기) → NAS 스캔
 *   → 프로젝트(광고주별 · 시간순 · 연·월별) → 카드 호버 재생 → 프로젝트 시트(재생)
 *   → 시트 [복사] → NAS 로 복사 → 시트 [보관] → 외장 J 로 이동
 *   → USB K 프로젝트의 「삭제(복구 불가)」 빨간 시트 → 작업 기록에서 NAS 복사 되돌리기(영구 삭제 확인)
 *   → 저장 공간 화면 · 트리맵 · 설정(팀 설정) → 팀원 PC(member) 홈.
 * 디스크에서 결과를 확인한다. ★복사·이동·삭제에는 희소 파일이 아니라 작은 실제 파일만 쓴다.
 * 헤드리스 Chromium 은 H.264 를 못 풀어서 재생은 VP9 webm 으로 본다.
 * 콘솔 오류·페이지 오류·실패한 요청(4xx/5xx)이 하나라도 있으면 exit 1.
 * 스크린샷: _shots/e2e-*.png 와 최종 세트 _shots/final-*.png.
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
const WORK = path.resolve(process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'db-e2e-')));
fs.mkdirSync(WORK, { recursive: true });
const FAKE = path.join(WORK, 'lead');
const HOME = path.join(WORK, 'home-lead');
const MFAKE = path.join(WORK, 'member');
const MHOME = path.join(WORK, 'home-member');

const steps = [];
const log = s => { steps.push(s); console.log('  · ' + s); };
function assert(c, msg) { if (!c) throw new Error('확인 실패: ' + msg); }
const sleep = ms => new Promise(r => setTimeout(r, ms));

function writeSmall(base, rel, n) {
  const p = path.join(base, ...rel.split('/'));
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const b = Buffer.alloc(n);
  for (let i = 0; i < n; i++) b[i] = (i * 31 + n) & 255;
  fs.writeFileSync(p, b);
}
function ffmpeg(args) { execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...args], { stdio: 'ignore' }); }

/* 시험 프로젝트 — 전부 작은 실제 파일(복사·이동이 실제로 바이트를 옮긴다) */
const PROJ = '261027_소담코스_청귤_종단시험_KMS';
const KPROJ = '261020_한결_전달본_종단삭제_HYC';
function addProjects() {
  const p = path.join(FAKE, 'D', 'Projects', PROJ);
  fs.mkdirSync(path.join(p, '렌더'), { recursive: true });
  const a = path.join(p, '렌더', 'GD_teaser.webm');
  const b = path.join(p, '렌더', 'GD_cutdown.webm');
  ffmpeg(['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25', '-t', '6', '-c:v', 'libvpx-vp9', '-b:v', '300k', '-deadline', 'realtime', '-cpu-used', '8', '-pix_fmt', 'yuv420p', a]);
  ffmpeg(['-f', 'lavfi', '-i', 'smptehdbars=size=640x360:rate=25', '-t', '5', '-c:v', 'libvpx-vp9', '-b:v', '200k', '-deadline', 'realtime', '-cpu-used', '8', '-pix_fmt', 'yuv420p', b]);
  const now = Date.now() / 1000;
  fs.utimesSync(b, now - 3600, now - 3600);
  fs.utimesSync(a, now - 60, now - 60);         // teaser 가 가장 최근 = 대표 영상
  writeSmall(FAKE, `D/Projects/${PROJ}/소스/소재_원본.psd`, 1200000);   // 1MB 넘게(프로젝트 최소 크기)
  writeSmall(FAKE, `D/Projects/${PROJ}/${PROJ}.aep`, 90000);
  writeSmall(FAKE, `K/전달용/${KPROJ}/최종/납품.mp4`, 1100000);
  writeSmall(FAKE, `K/전달용/${KPROJ}/메모.txt`, 20000);
}
function treeBytes(dir) {
  let n = 0;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    n += e.isDirectory() ? treeBytes(p) : fs.statSync(p).size;
  }
  return n;
}

function startServer(fake, home) {
  const port = 8790 + Math.floor(Math.random() * 400) + 100;
  const env = Object.assign({}, process.env, { DRIVEBOARD_HOME: home, DRIVEBOARD_NO_UPDATE_CHECK: '1' });
  delete env.DRIVEBOARD_FAKE;
  const proc = spawn('python3', [path.join(ROOT, 'app.py'), '--no-open', '--fake', fake, '--port', String(port)],
    { env, stdio: ['ignore', 'pipe', 'pipe'] });
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
function stopServer(s) {
  return new Promise(res => { if (!s || s.proc.exitCode != null) return res(); s.proc.once('exit', () => res()); s.proc.kill(); setTimeout(res, 4000); });
}
async function apiOf(url) {
  const html = await (await fetch(url)).text();
  const tok = (html.match(/name="db-token" content="([^"]+)"/) || [])[1];
  assert(tok, 'db-token 없음');
  return async (p, body) => {
    const r = await fetch(url.replace(/\/$/, '') + p, { method: body ? 'POST' : 'GET', headers: Object.assign({ 'X-DB-Token': tok }, body ? { 'Content-Type': 'application/json' } : {}), body: body ? JSON.stringify(body) : undefined });
    return r.json();
  };
}
const allProjects = d => d.groups.flatMap(g => g.projects);

(async () => {
  // ── 1. 가짜 드라이브 + 작은 실제 파일
  execFileSync('python3', [path.join(ROOT, 'tests', 'make_fake_drives.py'), FAKE, '--scale', 'demo', '--profile', 'lead', '--clean'], { stdio: 'ignore' });
  execFileSync('python3', [path.join(ROOT, 'tests', 'make_fake_drives.py'), MFAKE, '--scale', 'demo', '--profile', 'member', '--clean'], { stdio: 'ignore' });
  for (const h of [HOME, MHOME]) fs.rmSync(h, { recursive: true, force: true });
  addProjects();
  const NAS = fs.readdirSync(FAKE).find(n => n.startsWith('N-'));
  assert(NAS, 'lead 구성에 NAS 폴더(N-*)가 있어야 해요');
  const projBytes = treeBytes(path.join(FAKE, 'D', 'Projects', PROJ));
  log(`fake drives: ${WORK} (NAS ${NAS}, 시험 프로젝트 ${projBytes}B)`);

  let srv = await startServer(FAKE, HOME);
  let msrv = null;
  log('lead server ' + srv.url);
  const api = await apiOf(srv.url);
  const browser = await chromium.launch();
  const errors = [];
  let failed = null;
  const watch = (page, tag) => {
    page.on('console', m => { if (m.type() === 'error') errors.push(`console(${tag}): ${m.text()}`); });
    page.on('pageerror', e => errors.push(`pageerror(${tag}): ${(e && e.stack) || e}`));
    page.on('requestfailed', r => {
      const u = r.url();
      // <video> 가 바꿔 끼울 때 끊는 /media 요청은 정상이다
      if (!u.endsWith('favicon.ico') && !/\/media\//.test(u)) errors.push(`requestfailed(${tag}): ${u} ${(r.failure() && r.failure().errorText)}`);
    });
    page.on('response', r => { if (r.status() >= 400 && !r.url().endsWith('favicon.ico')) errors.push(`http ${r.status()}(${tag}): ${r.url()}`); });
  };
  try {
    const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, deviceScaleFactor: 1, acceptDownloads: true });
    const page = await ctx.newPage();
    watch(page, 'lead');
    const shot = async (name, settle = 500, keepMouse = false) => {
      if (!keepMouse) await page.mouse.move(2, 2).catch(() => { });
      await page.waitForTimeout(settle);
      await page.screenshot({ path: path.join(SHOTS, `${name}.png`) });
      log(`shot ${name}.png`);
    };
    const st = () => page.evaluate(() => window.DriveBoard.state);
    const clearToasts = () => page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));
    const waitIdle = async (ms = 60000) => {
      await page.waitForFunction(() => {
        const S = window.DriveBoard.state;
        return !S.busy && !Object.values(S.scans || {}).some(s => s.phase === 'scan' || s.phase === 'finalize')
          && !(S.jobs || []).some(j => j.status === 'running' || j.status === 'queued');
      }, null, { timeout: ms });
    };
    // 화면에 보이는 카드의 포스터가 다 정해질 때까지(그려짐 · 실패 · 영상 없음)
    const postersSettled = () => page.waitForFunction(() => {
      const cs = [...document.querySelectorAll('.proj-host .pc')].filter(c => { const r = c.getBoundingClientRect(); return r.width > 0 && r.bottom > 0 && r.top < innerHeight && r.left < innerWidth; });
      return cs.length && cs.every(c => c.classList.contains('nopv') || c.classList.contains('pvfail') || c.querySelector('.po-img.on'));
    }, null, { timeout: 45000 }).catch(() => log('보이는 카드 중 포스터가 아직 없는 것이 있어요'));
    const scanned = L => page.waitForFunction(L => { const d = window.DriveBoard.state.drives.find(x => x.letter === L); return d && d.scanned; }, L, { timeout: 60000 });

    // ── 2. 처음 실행 → 모두 스캔(외장 J·USB K 는 빠진다)
    await page.goto(srv.url, { waitUntil: 'domcontentloaded' });
    log('visibilityState=' + await page.evaluate(() => document.visibilityState));
    await page.waitForSelector('.first-run', { timeout: 15000 });
    await shot('e2e-first-run', 900);
    await page.click('.first-run .btn.primary');
    await page.waitForFunction(() => {
      const s = window.DriveBoard.state;
      return s.drives.length === 9 && s.drives.filter(d => d.scan).every(d => d.scanned);
    }, null, { timeout: 90000 });
    await waitIdle();
    let S = await st();
    assert(S.drives.filter(d => d.scanned).length === 7, '모두 스캔 = 내장 7개');
    assert(!S.drives.find(d => d.letter === 'J').scanned && !S.drives.find(d => d.letter === 'K').scanned, 'J·K 는 모두 스캔에서 빠진다');

    // ── 3. 외장 J · USB K 카드의 [스캔]
    for (const L of ['J', 'K']) {
      const c = page.locator(`.dcard[data-l="${L}"]`);
      await c.scrollIntoViewIfNeeded();
      await c.locator('.dc-foot .btn').click();
      await scanned(L);
      await waitIdle();
    }
    log('J, K scanned');

    // ── 4. NAS 추가: 장치 이름만 → 공유 찾기 → 체크 → 추가 → [스캔]
    await page.click('.rail-label[data-g="net"] .ibtn');
    await page.waitForSelector('.sheet.show .nas-add');
    await page.fill('.sheet.show .nas-add .addrow .tin', 'ORICO');
    await page.click('.sheet.show .nas-add .addrow .btn');
    await page.waitForSelector('.sheet.show .shrow.on');
    await shot('final-nas-add', 500);
    await page.click('.sheet.show .sh-f .btn.primary');
    await page.waitForSelector(`.rl[data-l="${NAS}"]`, { timeout: 10000 });
    await page.waitForSelector('.sheet.show', { state: 'detached', timeout: 5000 }).catch(() => { });
    await clearToasts();
    const nCard = page.locator(`.dcard[data-l="${NAS}"]`);
    await nCard.scrollIntoViewIfNeeded();
    await nCard.locator('.dc-foot .btn').click();
    await scanned(NAS);
    await waitIdle();
    log('NAS added + scanned: ' + NAS);
    await clearToasts();
    await page.evaluate(() => { document.getElementById('view').scrollTop = 0; });
    await page.waitForSelector('.rec', { timeout: 10000 }).catch(() => log('추천 카드 없음'));
    await shot('final-home-lead', 1300);

    // ── 5. 프로젝트 내비(실제 셸 경로) — 광고주별
    const pc = await api('/api/projects?group=client');
    const proj = allProjects(pc).find(p => p.name === PROJ);
    const kproj = allProjects(pc).find(p => p.name === KPROJ);
    assert(proj && proj.drive === 'D' && proj.preview && proj.preview.ext === 'webm', '시험 프로젝트(D, webm 대표)');
    assert(kproj && kproj.drive === 'K', 'K 의 시험 프로젝트');
    log(`projects: ${pc.total}, clients ${pc.clients.length}`);
    await page.click('.nv[data-nav="projects"]');
    await page.waitForSelector('.proj-host .pj');
    await page.waitForFunction(n => document.querySelectorAll('.proj-host .pc').length === n, pc.total, { timeout: 15000 });
    assert(await page.locator('.nv[data-nav="projects"].on').count() === 1, '내비 「프로젝트」 선택');
    assert(await page.locator('.pj-sec').count() === pc.groups.length, '광고주 섹션 수 = API 그룹 수');
    assert((await page.locator('.pj-sec .pj-sh .nm b').first().textContent()) === pc.groups[0].title, '첫 섹션 = 용량 큰 광고주');
    await postersSettled();
    await shot('final-projects-client', 600);

    // 시간순 · 연·월별
    await page.click('.pj-tabs button[data-id="time"]');
    const pt = await api('/api/projects?group=time');
    await page.waitForFunction(n => document.querySelectorAll('.pj-mh').length === n, pt.groups.length, { timeout: 10000 });
    assert((await page.locator('.pj-mh h3').first().textContent()) === pt.groups[0].title, '시간순 첫 구분선 = 최신 달');
    await postersSettled();
    await shot('final-projects-time', 600);
    await page.click('.pj-tabs button[data-id="month"]');
    await page.waitForSelector('.pj-chart .pj-col');
    const pm = await api('/api/projects?group=month');
    assert(await page.locator('.pj-col').count() === 12, '12칸 막대');
    assert(await page.locator('.pj-mon').count() === pm.groups.length, '월 섹션 수');
    await postersSettled();
    await shot('final-projects-month', 600);
    await page.click('.pj-tabs button[data-id="client"]');
    await page.waitForSelector('.pj-sec');
    log('grouping ok');

    // ── 6. 호버 재생(0.25초 뒤 음소거 재생)
    const card = k => page.locator(`.pc[data-k="${k}"]`);
    await page.fill('.pj-q input', '청귤 종단시험');
    await page.waitForFunction(k => { const c = document.querySelectorAll('.proj-host .pc'); return c.length === 1 && c[0].dataset.k === k; }, proj.key, { timeout: 8000 });
    await page.waitForTimeout(400);
    const wc = card(proj.key);
    const box = await wc.locator('.pc-po').boundingBox();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await page.waitForFunction(k => { const c = document.querySelector(`.pc[data-k="${k}"]`); const v = c && c.querySelector('video'); return c.classList.contains('playing') && v && v.currentTime > 0.3; }, proj.key, { timeout: 8000 });
    assert(await wc.evaluate(e => e.querySelector('video').muted), '호버 재생은 음소거');
    await shot('e2e-hover', 0, true);
    await page.mouse.move(2, 2);
    await page.waitForFunction(() => !document.querySelector('.proj-host .pc video'), null, { timeout: 4000 });
    log('hover play ok');

    // ── 7. 프로젝트 시트 — 재생
    await wc.locator('.pc-t').click();
    await page.waitForSelector('.pj-side.show');
    await page.waitForFunction(() => document.querySelectorAll('.ps-v').length >= 2 && !document.querySelector('.ps-v .ps-sk'), null, { timeout: 10000 });
    await page.keyboard.press(' ');
    await page.waitForFunction(() => { const v = document.querySelector('.pp video.on'); return v && !v.paused && v.currentTime > 0.8; }, null, { timeout: 8000 });
    await page.mouse.move(700, 300);
    await shot('final-project-sheet', 300, true);
    await page.keyboard.press(' ');

    // ── 8. 시트 [복사] → 셸의 복사 팝오버(NAS + 폴더 선택) → NAS 로 복사
    await page.click('.pj-side .sd-f .btn:has-text("복사")');
    await page.waitForFunction(() => !document.querySelector('.pj-side'), null, { timeout: 3000 });
    await page.waitForSelector(`.mvpop.copy .mvrow[data-l="${NAS}"]`);
    await page.waitForFunction(() => !document.querySelector('.mvpop .bar.loading'), null, { timeout: 10000 });
    assert(await page.locator('.mvpop .mvrow.pick').count() === 1, '「폴더 선택…」 줄');
    assert(await page.locator('.mvpop .mv-grp').count() >= 3, '내장·외장·NAS 묶음');
    await page.waitForFunction(L => { const r = document.querySelector(`.mvpop .mvrow[data-l="${L}"]`); return r && !r.disabled; }, NAS, { timeout: 10000 });
    await page.evaluate(L => document.querySelector(`.mvpop .mvrow[data-l="${L}"]`).scrollIntoView({ block: 'nearest' }), NAS);
    await shot('final-copy-popover', 400);
    await page.click(`.mvpop .mvrow[data-l="${NAS}"]`);
    await page.waitForSelector('.sheet.show .stats3 .stat .v');
    assert(/복사할까요/.test(await page.locator('.sheet.show h3').innerText()), '복사 확인 시트');
    await shot('e2e-copy-sheet', 600);
    await page.click('.sheet.show .sh-f .btn.primary');
    await page.waitForSelector('.toast:has-text("복사했어요")', { timeout: 30000 });
    const nasCopy = path.join(FAKE, NAS, 'Projects', PROJ);   // 같은 상대 경로로 간다
    assert(fs.existsSync(path.join(nasCopy, '렌더', 'GD_teaser.webm')) && treeBytes(nasCopy) === projBytes, 'NAS 에 같은 크기의 복사본');
    assert(fs.existsSync(path.join(FAKE, 'D', 'Projects', PROJ, '렌더', 'GD_teaser.webm')), '원본은 그대로');
    await shot('e2e-copy-done', 500);
    await waitIdle();
    const copyJob = (await st()).jobs.find(j => j.type === 'copy' && j.status === 'done');
    assert(copyJob && copyJob.undoable, '복사 작업이 되돌릴 수 있게 남는다');
    log('copy → NAS ok');

    // NAS 쪽에도 프로젝트로 잡힌다(대상만 다시 훑어 붙임)
    await page.waitForFunction(n => [...document.querySelectorAll('.proj-host .pc')].filter(c => c.textContent.includes('종단시험')).length >= 2, null, { timeout: 15000 })
      .catch(() => log('NAS 복사본 카드가 화면에 아직 없음(검색 결과 갱신 지연)'));
    const afterCopy = allProjects(await api('/api/projects?group=client&q=' + encodeURIComponent('청귤 종단시험')));
    assert(afterCopy.some(p => p.drive === NAS) && afterCopy.some(p => p.drive === 'D'), 'API: D 와 NAS 양쪽에 프로젝트 ' + JSON.stringify(afterCopy.map(p => p.drive)));

    // ── 9. 시트 [다른 위치로 보관] → 외장 J 로 이동
    await clearToasts();
    await card(proj.key).locator('.pc-t').click();
    await page.waitForSelector('.pj-side.show');
    await page.click('.pj-side .sd-f .btn:has-text("다른 위치로 보관")');
    await page.waitForFunction(() => !document.querySelector('.pj-side'), null, { timeout: 3000 });
    await page.waitForSelector('.mvpop.move .mvrow[data-l="J"]');
    await page.waitForFunction(() => { const r = document.querySelector('.mvpop .mvrow[data-l="J"]'); return r && !r.disabled && !document.querySelector('.mvpop .bar.loading'); }, null, { timeout: 10000 });
    await shot('e2e-move-popover', 300);
    await page.click('.mvpop .mvrow[data-l="J"]');
    await page.waitForSelector('.sheet.show .stats3 .stat .v');
    await page.click('.sheet.show .sh-f .btn.primary');
    await page.waitForSelector('.toast:has-text("옮겼어요")', { timeout: 30000 });
    const jDest = path.join(FAKE, 'J', 'Projects', PROJ);
    assert(!fs.existsSync(path.join(FAKE, 'D', 'Projects', PROJ)), 'D 의 원본이 사라졌다');
    assert(fs.existsSync(jDest) && treeBytes(jDest) === projBytes, 'J 에 같은 크기로 옮겨졌다');
    await waitIdle();
    await shot('e2e-move-done', 500);
    const afterMove = allProjects(await api('/api/projects?group=client&q=' + encodeURIComponent('청귤 종단시험')));
    assert(afterMove.some(p => p.drive === 'J') && !afterMove.some(p => p.drive === 'D'), 'API: 프로젝트가 J 로 ' + JSON.stringify(afterMove.map(p => p.drive)));
    log('move → J ok');

    // ── 10. USB K 프로젝트: 휴지통 대신 「삭제(복구 불가)」 빨간 시트
    await clearToasts();
    await page.fill('.pj-q input', '종단삭제');
    await page.waitForFunction(k => { const c = document.querySelectorAll('.proj-host .pc'); return c.length === 1 && c[0].dataset.k === k; }, kproj.key, { timeout: 8000 });
    await card(kproj.key).locator('.pc-t').click();
    await page.waitForSelector('.pj-side.show');
    const binBtn = page.locator('.pj-side .sd-f .btn.danger');
    assert(/삭제\(복구 불가\)/.test(await binBtn.innerText()), '휴지통 없는 곳의 시트 버튼 = 「삭제(복구 불가)」');
    await binBtn.click();
    await page.waitForSelector('.sheet.show.danger .ckrow');
    assert(await page.locator('.sheet.show .sh-f .btn.solid-danger[disabled]').count() === 1, '체크 전에는 잠김');
    await shot('final-nobin-delete', 600);
    await page.click('.sheet.show .ckrow');
    await page.click('.sheet.show .sh-f .btn.solid-danger:not([disabled])');
    await page.waitForSelector('.toast:has-text("영구 삭제했어요")', { timeout: 20000 });
    assert(!fs.existsSync(path.join(FAKE, 'K', '전달용', KPROJ)), 'K 에서 지워졌다');
    const kbin = path.join(FAKE, 'K', '.fakebin');
    assert(!fs.existsSync(kbin) || !fs.readdirSync(kbin).some(n => n.includes('종단삭제')), '휴지통을 거치지 않았다');
    await page.waitForFunction(k => !document.querySelector(`.pc[data-k="${k}"]`), kproj.key, { timeout: 10000 });
    await waitIdle();
    log('no-bin delete on K ok');

    // ── 11. 작업 기록에서 NAS 복사 되돌리기 = 복사본 영구 삭제(확인 체크)
    //    ①원본을 J 로 옮긴 뒤에는 원본 자리가 비어 「복사본이 유일본」 → 지우지 않고 남겨야 한다(안전 규칙)
    //    ②이동을 되돌려 원본이 제자리로 오면 복사 되돌리기가 복사본만 지운다
    const openJobs = async () => {
      await clearToasts();
      if (!(await page.locator('.jobpanel').count())) await page.click('#jobPill');
      await page.waitForSelector('.jobpanel .jitem');
    };
    const undoCopy = async () => {
      await openJobs();
      const b = page.locator('.jobpanel .btn:has-text("되돌리기(복사본 지우기)")');
      assert(await b.count() === 1, '작업 기록에 복사 되돌리기 버튼');
      await b.click();
      await page.waitForSelector('.sheet.show .ckrow');
      assert(await page.locator('.sheet.show .sh-f .btn.solid-danger[disabled]').count() === 1, '복사본 지우기는 체크 전 잠김');
      await page.click('.sheet.show .ckrow');
      await page.click('.sheet.show .sh-f .btn.solid-danger:not([disabled])');
    };
    await openJobs();
    await shot('e2e-jobs', 400);
    await undoCopy();
    await page.waitForSelector('.toast:has(.ti.err):has-text("실패")', { timeout: 20000 });
    assert(/유일본/.test(await page.locator('.toast:has(.ti.err)').first().innerText()), '원본이 없으면 복사본을 남긴다는 안내');
    assert(fs.existsSync(nasCopy) && treeBytes(nasCopy) === projBytes, '원본이 옮겨진 동안에는 NAS 복사본을 지우지 않는다');
    await waitIdle();
    log('undo copy refused while original moved away (copy kept)');
    // 이동 되돌리기(J → D)
    await openJobs();
    await page.locator('.jobpanel .jitem:has-text("→ J:") .btn:has-text("되돌리기")').click();
    await page.waitForSelector('.toast:has-text("되돌렸어요")', { timeout: 30000 });
    await waitIdle();
    assert(fs.existsSync(path.join(FAKE, 'D', 'Projects', PROJ)) && !fs.existsSync(jDest), '이동 되돌리기: D 로 돌아왔다');
    // 이제 복사 되돌리기
    await undoCopy();
    await page.waitForSelector('.toast:has-text("되돌렸어요")', { timeout: 20000 });
    assert(!fs.existsSync(nasCopy), 'NAS 복사본이 지워졌다');
    assert(treeBytes(path.join(FAKE, 'D', 'Projects', PROJ)) === projBytes, '원본은 그대로');
    await waitIdle();
    const afterUndo = allProjects(await api('/api/projects?group=client&q=' + encodeURIComponent('청귤 종단시험')));
    assert(afterUndo.length === 1 && afterUndo[0].drive === 'D', 'API: 프로젝트는 D 에만 ' + JSON.stringify(afterUndo.map(p => p.drive)));
    const nasLeft = path.join(FAKE, NAS, 'Projects');
    assert(!fs.existsSync(nasLeft) || !fs.readdirSync(nasLeft).length || fs.readdirSync(nasLeft).every(n => !n.includes('종단시험')), 'NAS 에 남은 것 없음');
    log('undo move + undo copy (permanent delete on NAS) ok');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    await page.fill('.pj-q input', '');
    await page.keyboard.press('Escape');

    // ── 12. 저장 공간: 드라이브 상세 · 목록 · 트리맵
    await clearToasts();
    await page.click('.nv[data-nav="storage"]');
    await page.waitForSelector('.hero');
    await page.click('.rl[data-l="D"]');
    await page.waitForSelector('.crow');
    await shot('e2e-drive', 1000);
    await page.locator('.screen:not(.leave) .tabrow .tabs button', { hasText: '트리맵' }).click();
    await page.waitForSelector('.tm-root[data-state="ready"]', { timeout: 10000 }).catch(() => log('트리맵 ready 상태를 못 봤어요'));
    await shot('final-treemap', 1300);

    // ── 13. 설정 — 팀 설정 내보내기/가져오기
    await page.click('#setBtn');
    await page.waitForSelector('.sheet.show .set-nav');
    await page.click('.set-nav button[data-s="team"]');
    await page.waitForTimeout(700);
    const [dl] = await Promise.all([page.waitForEvent('download'), page.click('.sheet.show .td-btns .btn >> nth=0')]);
    const dlPath = path.join(WORK, 'team-export.json');
    await dl.saveAs(dlPath);
    assert(JSON.parse(fs.readFileSync(dlPath, 'utf8')).format === 'driveboard-team', '팀 설정 내보내기');
    const imp = path.join(WORK, 'team-import.json');
    fs.writeFileSync(imp, JSON.stringify({ format: 'driveboard-team', version: 1, workers: { HYC: '팀원 B' }, client_aliases: { '한결': '한결증권' }, rules: [] }));
    await page.setInputFiles('.sheet.show .td-btns input[type=file]', imp);
    await page.waitForSelector('.sheet.show .tdiff .td-g');
    await page.evaluate(() => { const n = document.querySelector('.set-nav button[data-s="team"]'); if (n) n.click(); });
    await page.waitForTimeout(700);
    await shot('final-settings-team', 300);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);

    // ── 14. 팀원 PC(member, C + D)
    msrv = await startServer(MFAKE, MHOME);
    log('member server ' + msrv.url);
    const ctx3 = await browser.newContext({ viewport: { width: 1360, height: 860 } });
    const p3 = await ctx3.newPage();
    watch(p3, 'member');
    await p3.goto(msrv.url, { waitUntil: 'domcontentloaded' });
    await p3.waitForSelector('.first-run');
    await p3.click('.first-run .btn.primary');
    await p3.waitForFunction(() => window.DriveBoard.state.drives.length === 2 && window.DriveBoard.state.drives.every(d => d.scanned), null, { timeout: 60000 });
    await p3.waitForSelector('.dgrid.pair .dcard.big');
    await p3.waitForTimeout(1500);
    await p3.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));
    await p3.mouse.move(2, 2);
    await p3.waitForTimeout(400);
    await p3.screenshot({ path: path.join(SHOTS, 'final-home-member.png') });
    log('shot final-home-member.png');
    await p3.click('.nv[data-nav="projects"]');
    await p3.waitForSelector('.proj-host .pc', { timeout: 15000 });
    await p3.waitForTimeout(1200);
    await p3.screenshot({ path: path.join(SHOTS, 'e2e-member-projects.png') });
    log('shot e2e-member-projects.png');
    await ctx3.close();
  } catch (e) {
    failed = e;
  } finally {
    await browser.close();
    await stopServer(srv);
    await stopServer(msrv);
  }
  if (failed) console.error('FAIL:', (failed && failed.stack) || failed);
  if (errors.length) { console.error('콘솔/페이지 오류 ' + errors.length + '건:'); errors.forEach(e => console.error('  ' + e)); }
  if (failed || errors.length) { console.error(srv.log().slice(-3000)); process.exit(1); }
  console.log(`OK — ${steps.length} 단계, 오류 0 (작업 폴더 ${WORK})`);
})().catch(e => { console.error(e); process.exit(1); });
