#!/usr/bin/env node
/* DriveBoard UI 연기 시험(Playwright, 헤드리스 Chromium) — 1차 화면들이 v2 셸에서도 그대로 도는지.
 *
 *   NODE_PATH=/opt/node22/lib/node_modules node tests/ui_smoke.js [작업폴더]
 *   DB_URL=http://127.0.0.1:8790/ node tests/ui_smoke.js     # 이미 떠 있는 서버로(읽기 전용 단계만)
 *
 * 예전의 tests/mock_api.py(1차 계약 흉내)는 v2 에서 은퇴했다 — 실제 서버(app.py --no-open --fake)를
 * 가짜 드라이브 7개(--profile classic)로 직접 띄운다. 휴지통·이동은 작은 실제 파일로만 한다
 * (희소 파일을 옮기면 0 을 실제로 쓴다).
 * 콘솔 오류·페이지 오류가 하나라도 나면 실패(exit 1). 스크린샷은 _shots/ui-*.png.
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

const steps = [];
function log(s) { steps.push(s); console.log('  · ' + s); }

function writeSmall(base, rel, n) {
  const p = path.join(base, ...rel.split('/'));
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const b = Buffer.alloc(n);
  for (let i = 0; i < n; i++) b[i] = (i * 17 + n) & 255;
  fs.writeFileSync(p, b);
}

function startServer(fake, home) {
  const port = 8790 + Math.floor(Math.random() * 600) + 200;
  const env = Object.assign({}, process.env, { DRIVEBOARD_HOME: home, DRIVEBOARD_NO_UPDATE_CHECK: '1' });
  delete env.DRIVEBOARD_FAKE;
  const proc = spawn('python3', [path.join(ROOT, 'app.py'), '--no-open', '--fake', fake, '--port', String(port)], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '';
  return new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('server did not start: ' + out)), 30000);
    const on = d => { out += d; const m = out.match(/DriveBoard: (http:\/\/127\.0\.0\.1:\d+\/)/); if (m) { clearTimeout(t); res({ url: m[1], proc }); } };
    proc.stdout.on('data', on);
    proc.stderr.on('data', d => { out += d; });
    proc.on('exit', c => { clearTimeout(t); rej(new Error('server exited ' + c + ': ' + out)); });
  });
}

(async () => {
  let srv = null;
  let url = process.env.DB_URL;
  const own = !url;
  if (own) {
    const WORK = path.resolve(process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'db-smoke-')));
    const FAKE = path.join(WORK, 'fake');
    const HOME = path.join(WORK, 'home');
    execFileSync('python3', [path.join(ROOT, 'tests', 'make_fake_drives.py'), FAKE, '--scale', 'demo', '--profile', 'classic', '--no-video', '--clean'], { stdio: 'ignore' });
    fs.rmSync(HOME, { recursive: true, force: true });
    for (let i = 1; i <= 4; i++) writeSmall(FAKE, `D/_SMOKE/정리할폴더${i}/메모.txt`, 120000 + i * 3000);
    srv = await startServer(FAKE, HOME);
    url = srv.url;
    log('server ' + url + ' (' + WORK + ')');
  }
  const browser = await chromium.launch();
  const errors = [];
  let failed = null;
  try {
    const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    page.on('pageerror', e => errors.push('pageerror: ' + (e && e.stack || e)));
    page.on('requestfailed', r => { if (!r.url().endsWith('favicon.ico') && !/\/media\//.test(r.url())) errors.push('requestfailed: ' + r.url() + ' ' + (r.failure() && r.failure().errorText)); });
    const shot = async (name, settle = 500) => {
      await page.waitForTimeout(settle);
      await page.screenshot({ path: path.join(SHOTS, `ui-${name}.png`) });
      log(`shot ui-${name}.png`);
    };
    const S = () => page.evaluate(() => window.DriveBoard.state);
    const home = async () => { await page.click('.nv[data-nav="storage"]'); await page.waitForSelector('.screen:not(.leave) .hero'); };

    // 0. 처음 실행(아무 드라이브도 스캔 안 됨) → 모두 스캔
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    log(`visibilityState=${await page.evaluate(() => document.visibilityState)}`);
    await page.waitForSelector('.dcard', { timeout: 15000 });
    if (await page.locator('.first-run').count()) {
      await shot('home-fresh', 900);
      await page.click('.first-run .btn.primary');
      await page.waitForSelector('.dcard.scanning', { timeout: 5000 }).catch(() => log('스캔 중 카드를 못 봤어요(빠르게 끝남)'));
      await shot('scanning', 900);
    }
    await page.waitForFunction(() => window.DriveBoard.state.drives.filter(d => d.scan).every(d => d.scanned) && !window.DriveBoard.state.busy, null, { timeout: 120000 });

    // 1. 홈
    await page.waitForSelector('.rec', { timeout: 10000 }).catch(() => log('추천 카드 없음'));
    await shot('home', 1300);
    const hb = await page.locator('.hero .bar').boundingBox();
    await page.mouse.move(hb.x + hb.width * 0.3, hb.y + hb.height / 2);
    await shot('home-tip', 350);
    await page.mouse.move(5, 5);
    const chip = page.locator('.hero .lg[data-c="c2"]');
    if (await chip.count()) { await chip.hover(); await shot('home-hl', 350); await page.mouse.move(5, 5); }

    // 2. 드라이브 상세
    const L = (await S()).drives.find(x => x.scanned && x.letter === 'D') ? 'D' : (await S()).drives.find(x => x.scanned).letter;
    await page.click(`.rl[data-l="${L}"]`);
    await page.waitForSelector('.crow');
    await page.waitForSelector('.mini-rows .mrow', { timeout: 8000 });
    await shot('drive', 900);

    // 3. 카테고리 목록 · 선택 · 이동/복사 팝오버 · 휴지통 시트(실행하지 않음)
    await page.locator('.screen:not(.leave) .crow[data-c^="c"]').first().click();
    await page.waitForFunction(() => document.querySelectorAll('.screen:not(.leave) .rows .row').length >= 6, null, { timeout: 10000 });
    await shot('list', 900);
    const rows = page.locator('.screen:not(.leave) .rows .row:not(.loose)');
    await rows.nth(0).locator('.ck').click();
    await rows.nth(2).click({ position: { x: 300, y: 20 } });
    await rows.nth(5).locator('.ck').click({ modifiers: ['Shift'] });
    await page.waitForSelector('#tray.show');
    const nsel = await page.evaluate(() => window.DriveBoard.state.sel.size);
    log(`selected=${nsel}`);
    if (nsel < 3) throw new Error('선택이 3개 미만: ' + nsel);
    await shot('select', 700);
    await page.click('#trMove');
    await page.waitForSelector('.mvpop .mvrow');
    await page.waitForFunction(() => !document.querySelector('.mvpop .bar.loading'), null, { timeout: 8000 });
    await shot('move', 500);
    await page.keyboard.press('Escape');
    await page.click('#trCopy');
    await page.waitForSelector('.mvpop.copy .mvrow');
    await page.waitForFunction(() => !document.querySelector('.mvpop .bar.loading'), null, { timeout: 8000 });
    await shot('copy', 500);
    await page.keyboard.press('Escape');
    await page.click('#trRecycle');
    await page.waitForSelector('.sheet.show .stats3 .stat .v');
    await shot('recycle-sheet', 600);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    await page.keyboard.press('Escape');

    // 4. 작은 실제 폴더로: 휴지통 → 행 접힘, 드래그 이동 → 되돌리기
    if (own) {
      await page.click(`.rl[data-l="D"]`);
      await page.locator('.screen:not(.leave) .tabrow .tabs button', { hasText: '폴더' }).click();
      await page.locator('.screen:not(.leave) .rows .row:has-text("_SMOKE")').first().click({ position: { x: 300, y: 20 } });
      await page.waitForFunction(() => [...document.querySelectorAll('.fcrumbs button.cur')].some(b => b.textContent === '_SMOKE'), null, { timeout: 8000 });
      await page.waitForTimeout(400);
      await shot('folders-in', 300);
      await page.locator('.rows .row:has-text("정리할폴더1") .ck').click();
      await page.click('#trRecycle');
      await page.waitForSelector('.sheet.show .stats3 .stat .v');
      await page.click('.sheet .sh-f .btn.danger');
      await page.waitForSelector('.jobpill.active', { timeout: 5000 }).catch(() => log('작업 알약 활성 상태를 못 봤어요(빠르게 끝남)'));
      await page.waitForSelector('.toast:has-text("휴지통으로 보냈어요")', { timeout: 15000 });
      await shot('after-recycle', 900);
      await page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));
      const dragRow = page.locator('.rows .row:has-text("정리할폴더2")');
      const box = await dragRow.boundingBox();
      const tgt = await page.locator('.rl[data-l="E"]').boundingBox();
      await page.mouse.move(box.x + 300, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + 280, box.y + box.height / 2 - 20, { steps: 4 });
      await page.mouse.move(tgt.x + tgt.width / 2, tgt.y + tgt.height / 2, { steps: 18 });
      await page.waitForTimeout(450);
      await shot('drag', 100);
      await page.mouse.up();
      await page.waitForSelector('.sheet.show .stats3 .stat .v', { timeout: 8000 });
      await shot('move-sheet', 600);
      await page.click('.sheet .sh-f .btn.primary');
      await page.waitForTimeout(260);
      await shot('move-fly', 0);
      await page.waitForSelector('.toast .btn.lime:has-text("되돌리기")', { timeout: 15000 });
      await shot('after-move', 700);
      await page.click('.toast .btn.lime:has-text("되돌리기")');
      await page.waitForSelector('.toast:has-text("되돌렸어요")', { timeout: 15000 });
      await shot('after-undo', 900);
      // 휴지통 드롭존 → 휴지통 확인 시트
      const r2 = page.locator('.rows .row:has-text("정리할폴더3")');
      const b2 = await r2.boundingBox();
      const bz = await page.locator('#binZone').boundingBox();
      await page.mouse.move(b2.x + 300, b2.y + b2.height / 2);
      await page.mouse.down();
      await page.mouse.move(b2.x + 260, b2.y + 10, { steps: 4 });
      await page.mouse.move(bz.x + bz.width / 2, bz.y + bz.height / 2, { steps: 16 });
      await page.waitForTimeout(350);
      await shot('drag-bin', 50);
      await page.mouse.up();
      await page.waitForSelector('.sheet.show');
      await page.waitForTimeout(400);
      const title = await page.locator('.sheet.show h3').innerText();
      log('bin drop sheet: ' + title);
      if (!/휴지통으로 보낼까요/.test(title)) throw new Error('휴지통 드롭 후 엉뚱한 시트: ' + title);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);
    }

    // 5. 작업 패널
    await page.click('#jobPill');
    await page.waitForSelector('.jobpanel');
    await shot('jobs', 400);
    await page.keyboard.press('Escape');

    // 6. 폴더 탭 · 트리맵 탭
    await page.click(`.rl[data-l="${L}"]`);
    await page.waitForSelector('.screen:not(.leave) .tabs button');
    await page.locator('.screen:not(.leave) .tabrow .tabs button', { hasText: '폴더' }).click();
    await page.waitForFunction(() => document.querySelectorAll('.screen:not(.leave) .rows .row').length >= 2, null, { timeout: 8000 });
    await shot('folders', 800);
    await page.locator('.screen:not(.leave) .tabrow .tabs button', { hasText: '트리맵' }).click();
    await shot('treemap', 1400);

    // 7. 설정
    await page.click('#setBtn');
    await page.waitForSelector('.sheet.show .set-sec');
    await shot('settings', 600);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);

    // 8. 중복 → 우클릭 메뉴
    await home();
    await page.waitForSelector('.rec');
    const dupRec = page.locator('.rec', { hasText: '중복' });
    if (await dupRec.count()) {
      await dupRec.first().click();
      await page.waitForSelector('.dg .row', { timeout: 10000 });
      await shot('dupes', 700);
      await page.locator('.dg .row').first().click({ button: 'right', position: { x: 300, y: 18 } });
      await page.waitForSelector('.menu .mi');
      await shot('context', 300);
      await page.keyboard.press('Escape');
    } else log('중복 추천이 없어 중복 화면은 건너뜀');

    // 9. 검색(큰 파일 이름의 앞부분)
    await home();
    const term = await page.evaluate(async () => {
      const tok = document.querySelector('meta[name="db-token"]').content;
      const r = await fetch('/api/items?drive=*&view=files&sort=size&order=desc&limit=5', { headers: { 'X-DB-Token': tok } }).then(x => x.json());
      const it = (r.items || []).find(x => x.kind === 'file') || (r.items || [])[0];
      return it ? it.name.replace(/\.[^.]+$/, '').slice(0, 6) : 'render';
    });
    await page.keyboard.press('/');
    await page.keyboard.type(term);
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelectorAll('.screen:not(.leave) .rows .row').length >= 1, null, { timeout: 8000 });
    await shot('search', 700);

    // 10. 1100x720 (최소 크기)
    await page.setViewportSize({ width: 1100, height: 720 });
    await home();
    await shot('home-1100', 1300);
    await page.click(`.rl[data-l="${L}"]`);
    await page.waitForSelector('.screen:not(.leave) .crow');
    await shot('drive-1100', 900);
    await page.locator('.screen:not(.leave) .crow[data-c^="c"]').first().click();
    await page.waitForFunction(() => document.querySelectorAll('.screen:not(.leave) .rows .row').length >= 6, null, { timeout: 10000 });
    await page.locator('.screen:not(.leave) .rows .row:not(.loose)').nth(0).locator('.ck').click();
    await shot('list-1100', 700);

    // 11. 움직임 줄이기(prefers-reduced-motion)
    const ctx2 = await browser.newContext({ viewport: { width: 1360, height: 860 }, reducedMotion: 'reduce' });
    const p2 = await ctx2.newPage();
    p2.on('console', m => { if (m.type() === 'error') errors.push('console(reduced): ' + m.text()); });
    p2.on('pageerror', e => errors.push('pageerror(reduced): ' + (e && e.stack || e)));
    await p2.goto(url, { waitUntil: 'domcontentloaded' });
    await p2.waitForSelector('.dcard');
    await p2.waitForTimeout(400);
    await p2.screenshot({ path: path.join(SHOTS, 'ui-home-reduced.png') });
    log('shot ui-home-reduced.png');
    await ctx2.close();
  } catch (e) {
    failed = e;
  } finally {
    await browser.close();
    if (srv) srv.proc.kill();
  }
  if (failed) { console.error('FAIL:', failed && failed.stack || failed); }
  if (errors.length) { console.error('콘솔/페이지 오류 ' + errors.length + '건:'); errors.forEach(e => console.error('  ' + e)); }
  if (failed || errors.length) process.exit(1);
  console.log(`OK — ${steps.length} 단계, 오류 0`);
})();
