#!/usr/bin/env node
/* DriveBoard v2 셸 UI 시험(Playwright, 헤드리스 Chromium) — 실제 서버(app.py --fake)로 돈다.
 *
 *   NODE_PATH=/opt/node22/lib/node_modules node tests/ui_shell.js [작업폴더]
 *
 * 팀장 PC(lead: 내장 7 + 외장 J + USB K + NAS)와 팀원 PC(member: C+D) 두 구성을 띄워
 * 홈 · 레일 묶음 · NAS 추가 · 외장/USB/NAS 카드 · 연결 안 된 장치(서버를 끄고 드라이브 폴더를 치운 뒤 다시 띄움)
 * · 트레이 복사 · Ctrl 드래그 복사(+ 휴지통 없는 곳의 복사 되돌리기) · 휴지통 없는 곳의 영구 삭제
 * · 설정의 새 구획 · 업데이트 알약(로컬 가짜 GitHub) 을 실제로 눌러 보고 _shots/v2-shell-*.png 를 남긴다.
 * ★복사·삭제에는 희소 파일이 아니라 작은 실제 파일만 쓴다(희소 파일을 복사하면 0 을 실제로 쓴다).
 * 콘솔 오류·페이지 오류·실패한 요청이 하나라도 있으면 exit 1.
 */
'use strict';
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const { spawn, execFileSync } = require('child_process');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const SHOTS = path.join(ROOT, '_shots');
fs.mkdirSync(SHOTS, { recursive: true });
const WORK = path.resolve(process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'db-shell-')));
const LEAD = path.join(WORK, 'lead');
const MEMBER = path.join(WORK, 'member');
const HOME_L = path.join(WORK, 'home-lead');
const HOME_M = path.join(WORK, 'home-member');

const steps = [];
const log = s => { steps.push(s); console.log('  · ' + s); };
function assert(c, msg) { if (!c) throw new Error('확인 실패: ' + msg); }

function writeSmall(base, rel, n) {
  const p = path.join(base, ...rel.split('/'));
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const b = Buffer.alloc(n);
  for (let i = 0; i < n; i++) b[i] = (i * 31 + n) & 255;
  fs.writeFileSync(p, b);
}

/* 로컬 가짜 GitHub — 업데이트 확인(DRIVEBOARD_UPDATE_BASE, 루프백 http 만 허용) */
function fakeGitHub() {
  return new Promise(res => {
    const srv = http.createServer((req, rsp) => {
      rsp.writeHead(200, { 'Content-Type': 'application/json' });
      rsp.end(JSON.stringify({ tag_name: 'v9.9.9', html_url: 'https://github.com/minsk999/driveboard/releases/tag/v9.9.9', body: '시험용', prerelease: false, draft: false }));
    });
    srv.listen(0, '127.0.0.1', () => res({ srv, base: `http://127.0.0.1:${srv.address().port}` }));
  });
}

function startServer(fake, home, extraEnv = {}) {
  const port = 8790 + Math.floor(Math.random() * 600) + 300;
  const env = Object.assign({}, process.env, { DRIVEBOARD_HOME: home, DRIVEBOARD_NO_UPDATE_CHECK: '1' }, extraEnv);
  delete env.DRIVEBOARD_FAKE;
  if (extraEnv.DRIVEBOARD_UPDATE_BASE) delete env.DRIVEBOARD_NO_UPDATE_CHECK;
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
function stopServer(s) {
  return new Promise(res => { if (!s || s.proc.exitCode != null) return res(); s.proc.once('exit', () => res()); s.proc.kill(); setTimeout(res, 4000); });
}

(async () => {
  // 1. 가짜 드라이브 + 작은 실제 파일
  execFileSync('python3', [path.join(ROOT, 'tests', 'make_fake_drives.py'), LEAD, '--scale', 'demo', '--profile', 'lead', '--clean'], { stdio: 'ignore' });
  execFileSync('python3', [path.join(ROOT, 'tests', 'make_fake_drives.py'), MEMBER, '--scale', 'demo', '--profile', 'member', '--no-video', '--clean'], { stdio: 'ignore' });
  for (const h of [HOME_L, HOME_M]) fs.rmSync(h, { recursive: true, force: true });
  writeSmall(LEAD, 'D/_SHELL/복사할폴더/납품_v01.txt', 300000);
  writeSmall(LEAD, 'D/_SHELL/복사할폴더/sub/로그.txt', 120000);
  writeSmall(LEAD, 'D/_SHELL/드래그폴더/메모.txt', 180000);
  writeSmall(LEAD, 'K/_SHELL/지울폴더/임시.txt', 200000);
  writeSmall(MEMBER, 'C/_SHELL/팀원폴더/a.txt', 150000);
  fs.mkdirSync(path.join(MEMBER, 'C', '_SHELL_DEST'), { recursive: true });
  const NAS = fs.readdirSync(LEAD).find(n => n.startsWith('N-'));
  assert(NAS, 'lead 구성에 NAS 폴더(N-*)가 있어야 해요');
  log('fake drives: ' + WORK + ' (NAS ' + NAS + ')');

  const gh = await fakeGitHub();
  let srv = await startServer(LEAD, HOME_L, { DRIVEBOARD_UPDATE_BASE: gh.base });
  log('lead server ' + srv.url);
  const browser = await chromium.launch();
  const errors = [];
  let allowHttp = 0;
  let failed = null;
  let msrv = null;
  const watch = (page, tag) => {
    page.on('console', m => {
      if (m.type() !== 'error') return;
      const t = m.text();
      if (allowHttp > 0 && /status of 409/.test(t)) { allowHttp--; return; }
      errors.push(`console(${tag}): ${t}`);
    });
    page.on('pageerror', e => errors.push(`pageerror(${tag}): ${(e && e.stack) || e}`));
    page.on('requestfailed', r => { if (!r.url().endsWith('favicon.ico') && !/\/media\//.test(r.url())) errors.push(`requestfailed(${tag}): ${r.url()} ${(r.failure() && r.failure().errorText)}`); });
  };
  try {
    const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, deviceScaleFactor: 1, acceptDownloads: true });
    const page = await ctx.newPage();
    watch(page, 'lead');
    const shot = async (name, settle = 500) => {
      await page.waitForTimeout(settle);
      await page.screenshot({ path: path.join(SHOTS, `v2-shell-${name}.png`) });
      log(`shot v2-shell-${name}.png`);
    };
    const state = () => page.evaluate(() => window.DriveBoard.state);
    const clearToasts = () => page.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));
    const waitIdle = async (ms = 60000) => {
      await page.waitForFunction(() => {
        const S = window.DriveBoard.state;
        return !S.busy && !Object.values(S.scans || {}).some(s => s.phase === 'scan' || s.phase === 'finalize');
      }, null, { timeout: ms });
    };
    const scanned = L => page.waitForFunction(L => { const d = window.DriveBoard.state.drives.find(x => x.letter === L); return d && d.scanned; }, L, { timeout: 60000 });

    // ── 2. 처음 실행: 레일 묶음 · 업데이트 알약
    await page.goto(srv.url, { waitUntil: 'domcontentloaded' });
    log('visibilityState=' + await page.evaluate(() => document.visibilityState));
    await page.waitForSelector('.first-run', { timeout: 15000 });
    for (const g of ['int', 'ext', 'net']) assert(await page.locator(`.rail-label[data-g="${g}"]`).count() === 1, '레일 묶음 ' + g);
    assert(await page.locator('.rl-add').count() === 1, 'NAS 가 없으면 레일에 「NAS 추가」 줄');
    assert(await page.locator('.nv[data-nav="storage"].on').count() === 1, '내비 「저장 공간」 선택됨');
    await page.waitForSelector('#updPill:not([hidden])', { timeout: 15000 });
    const pillText = await page.locator('#updPill').innerText();
    assert(/9\.9\.9/.test(pillText), '업데이트 알약 문구: ' + pillText);
    const J0 = (await state()).drives.find(d => d.letter === 'J');
    assert(J0 && J0.external && J0.scan === false, 'J 는 외장, 기본 스캔 제외');
    await shot('lead-fresh', 900);

    // 업데이트 알약 → /api/open_url (브라우저는 열지 않는다 — 요청만 가로채 확인)
    let openedUrl = null;
    await page.route('**/api/open_url', async r => { openedUrl = JSON.parse(r.request().postData() || '{}').url; await r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true,"opened":true}' }); });
    await page.click('#updPill');
    await page.waitForTimeout(300);
    assert(openedUrl === 'https://github.com/minsk999/driveboard/releases/tag/v9.9.9', '알약이 릴리스 페이지를 연다: ' + openedUrl);
    await page.unroute('**/api/open_url');

    // ── 3. 모두 스캔(외장·USB 는 빠진다) → 홈
    await page.click('.first-run .btn.primary');
    await page.waitForFunction(() => window.DriveBoard.state.drives.filter(d => d.scan).every(d => d.scanned), null, { timeout: 90000 });
    await waitIdle();
    let st = await state();
    assert(!st.drives.find(d => d.letter === 'J').scanned && !st.drives.find(d => d.letter === 'K').scanned, '모두 스캔은 J·K 를 건너뛴다');
    await page.waitForSelector('.rec', { timeout: 10000 }).catch(() => log('추천 카드 없음'));
    await shot('lead-home', 1200);

    // ── 4. 외장·USB 카드 → 각자 [스캔]
    const jCard = page.locator('.dcard[data-l="J"]');
    const kCard = page.locator('.dcard[data-l="K"]');
    assert(/외장 SSD/.test(await jCard.locator('.kchip').first().innerText()), 'J 카드 종류 칩');
    assert(await kCard.locator('.kchip.nb:not([hidden])').count() === 1, 'K 카드에 「휴지통 없음」');
    assert(await kCard.locator('.sw').count() === 1, '스캔 제외 장치 카드에 스위치');
    await kCard.scrollIntoViewIfNeeded();
    await shot('lead-ext-cards', 400);
    await jCard.locator('.dc-foot .btn').click();
    await scanned('J');
    await kCard.locator('.dc-foot .btn').click();
    await scanned('K');
    await waitIdle();

    // ── 5. NAS 추가: 장치 이름만 → 공유 찾기 → 체크 → 이름 → 추가
    await page.click('.rail-label[data-g="net"] .ibtn');
    await page.waitForSelector('.sheet.show .nas-add');
    await page.fill('.sheet.show .nas-add .addrow .tin', 'ORICO');
    await page.click('.sheet.show .nas-add .addrow .btn');
    await page.waitForSelector('.sheet.show .shrow');
    assert(await page.locator('.sheet.show .shrow.on').count() === 1, '공유가 하나면 미리 체크');
    const lab = await page.inputValue('.sheet.show .nas-res > .tin');
    assert(/ORICO · 영상백업/.test(lab), '이름 기본값: ' + lab);
    await shot('nas-shares', 400);
    await page.click('.sheet.show .sh-f .btn.primary');
    await page.waitForSelector(`.rl[data-l="${NAS}"]`, { timeout: 10000 });
    await page.waitForSelector('.sheet.show', { state: 'detached', timeout: 5000 }).catch(() => { });
    assert(await page.locator('.rl-add').count() === 0, 'NAS 가 생기면 「NAS 추가」 줄이 빠진다');
    log('NAS added: ' + NAS);
    // 같은 위치를 직접 입력으로 다시 추가 → 「이미 추가한 위치예요」(409 는 예상한 응답)
    await page.waitForTimeout(400);
    await page.click('.rail-label[data-g="net"] .ibtn');
    await page.waitForSelector('.sheet.show .nas-add');
    await page.click('.sheet.show .nas-man-l .link');
    await page.fill('.sheet.show .nas-res .tin >> nth=0', '\\\\ORICO\\영상백업');
    allowHttp++;
    await page.click('.sheet.show .sh-f .btn.primary');
    await page.waitForSelector('.sheet.show .note.warn:has-text("이미 추가한")', { timeout: 5000 });
    await shot('nas-manual-exists', 300);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    // NAS 카드 → [스캔]
    const nCard = page.locator(`.dcard[data-l="${NAS}"]`);
    await nCard.scrollIntoViewIfNeeded();
    assert(await nCard.locator('.kchip.nb:not([hidden])').count() === 1, 'NAS 카드에 「휴지통 없음」');
    await nCard.locator('.dc-foot .btn').click();
    await scanned(NAS);
    await waitIdle();
    await page.evaluate(() => { const s = document.querySelector('.dsec[data-g="ext"]'); if (s) s.scrollIntoView({ block: 'start' }); });
    await shot('lead-devices', 700);

    // ── 6. 트레이 복사: D:\_SHELL\복사할폴더 → J
    const openShell = async L => {
      await page.click(`.rl[data-l="${L}"]`);
      await page.waitForSelector('.tabrow .tabs button');
      await page.locator('.screen:not(.leave) .tabrow .tabs button', { hasText: '폴더' }).click();
      await page.locator('.screen:not(.leave) .rows .row:has-text("_SHELL")').first().click({ position: { x: 300, y: 20 } });
      await page.waitForFunction(() => [...document.querySelectorAll('.fcrumbs button.cur')].some(b => b.textContent === '_SHELL'), null, { timeout: 8000 });
      await page.waitForTimeout(400);
    };
    await openShell('D');
    await page.locator('.rows .row:has-text("복사할폴더") .ck').click();
    await page.waitForSelector('#tray.show');
    assert(/휴지통으로/.test(await page.locator('#trRecycle').innerText()), 'D 는 휴지통 있음');
    await page.click('#trCopy');
    await page.waitForSelector('.mvpop.copy .mvrow[data-l="J"]');
    await page.waitForFunction(() => !document.querySelector('.mvpop .bar.loading'), null, { timeout: 10000 });
    assert(await page.locator('.mvpop .mv-grp').count() >= 3, '팝오버에 내장·외장·NAS 묶음');
    assert(await page.locator('.mvpop .mvrow.pick').count() === 1, '「폴더 선택…」 줄');
    const sameWhy = await page.locator('.mvpop .mvrow[data-l="D"] .why').innerText();
    assert(/폴더를 골라/.test(sameWhy), '복사는 같은 드라이브에서 폴더를 고르게 한다: ' + sameWhy);
    await shot('copy-pop', 300);
    await page.click('.mvpop .mvrow[data-l="J"]');
    await page.waitForSelector('.sheet.show .stats3 .stat .v');
    assert(/복사할까요/.test(await page.locator('.sheet.show h3').innerText()), '복사 확인 시트 제목');
    await shot('copy-sheet', 600);
    await page.click('.sheet.show .sh-f .btn.primary');
    await page.waitForTimeout(220);
    await shot('copy-fly', 0);
    await page.waitForSelector('.toast:has-text("복사했어요")', { timeout: 20000 });
    await shot('copy-done', 500);
    assert(fs.existsSync(path.join(LEAD, 'J', '_SHELL', '복사할폴더', 'sub', '로그.txt')), '복사본이 J 에 있다');
    assert(fs.existsSync(path.join(LEAD, 'D', '_SHELL', '복사할폴더', '납품_v01.txt')), '원본은 그대로');
    assert(await page.locator('.rows .row:has-text("복사할폴더")').count() === 1, '복사 뒤에도 원본 행이 남는다');

    // ── 7. Ctrl 드래그 = 복사 → NAS, 그리고 되돌리기(휴지통 없음 → 영구 삭제 확인)
    await waitIdle();
    await clearToasts();
    await page.waitForTimeout(400);
    const dr = page.locator('.rows .row:has-text("드래그폴더")').first();
    const box = await dr.boundingBox();
    const tgt = await page.locator(`.rl[data-l="${NAS}"]`).boundingBox();
    await page.mouse.move(box.x + 320, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + 300, box.y + 8, { steps: 4 });
    await page.mouse.move(tgt.x + tgt.width / 2, tgt.y + tgt.height / 2, { steps: 14 });
    await page.waitForTimeout(250);
    assert(/이동/.test(await page.locator('#dragChip .dmode').innerText()), 'Ctrl 없이 끌면 「이동」');
    await page.keyboard.down('Control');
    await page.mouse.move(tgt.x + tgt.width / 2 + 3, tgt.y + tgt.height / 2, { steps: 2 });
    await page.waitForTimeout(300);
    assert(await page.locator('#dragChip.copy').count() === 1, 'Ctrl 을 누르면 칩이 복사 모양');
    assert(/복사 \+/.test(await page.locator('#dragChip .dmode').innerText()), '칩 문구 「복사 +」');
    await shot('ctrl-drag', 100);
    await page.mouse.up();
    await page.keyboard.up('Control');
    await page.waitForSelector('.sheet.show .stats3 .stat .v', { timeout: 8000 });
    assert(/복사할까요/.test(await page.locator('.sheet.show h3').innerText()), 'Ctrl 드롭은 복사 시트');
    await page.click('.sheet.show .sh-f .btn.primary');
    await page.waitForSelector('.toast:has-text("복사했어요"):has-text("ORICO")', { timeout: 20000 });
    const nasCopy = path.join(LEAD, NAS, '_SHELL', '드래그폴더', '메모.txt');
    assert(fs.existsSync(nasCopy), 'NAS 에 복사본');
    await page.locator('.toast:has-text("복사했어요"):has-text("ORICO") .btn:has-text("되돌리기")').click();
    await page.waitForSelector('.sheet.show .ckrow');
    assert(await page.locator('.sheet.show .sh-f .btn.solid-danger[disabled]').count() === 1, '확인 전에는 지우기 버튼이 잠겨 있다');
    await shot('undo-copy-nobin', 400);
    await page.click('.sheet.show .ckrow');
    await page.click('.sheet.show .sh-f .btn.solid-danger');
    await page.waitForSelector('.toast:has-text("되돌렸어요")', { timeout: 20000 });
    assert(!fs.existsSync(nasCopy), '되돌리면 NAS 복사본이 지워진다');
    assert(fs.existsSync(path.join(LEAD, 'D', '_SHELL', '드래그폴더', '메모.txt')), '원본은 그대로');
    log('copy undo on NAS ok');

    // ── 8. 휴지통 없는 곳(K: USB) → 「삭제(복구 불가)」 · 체크박스 확인
    await waitIdle();
    await clearToasts();
    await openShell('K');
    await page.locator('.rows .row:has-text("지울폴더") .ck').click();
    await page.waitForSelector('#tray.show');
    assert(/삭제\(복구 불가\)/.test(await page.locator('#trRecycle').innerText()), '트레이 버튼이 「삭제(복구 불가)」');
    assert(await page.locator('#trRecycle.solid-danger').count() === 1, '빨간 버튼');
    await shot('nobin-tray', 400);
    await page.click('#trRecycle');
    await page.waitForSelector('.sheet.show .ckrow');
    assert(await page.locator('.sheet.show .sh-f .btn.solid-danger[disabled]').count() === 1, '체크 전에는 잠김');
    await shot('nobin-delete', 500);
    await page.click('.sheet.show .ckrow');
    assert(await page.locator('.sheet.show .sh-f .btn.solid-danger:not([disabled])').count() === 1, '체크하면 풀림');
    await page.click('.sheet.show .sh-f .btn.solid-danger');
    await page.waitForSelector('.toast:has-text("영구 삭제했어요")', { timeout: 20000 });
    assert(!fs.existsSync(path.join(LEAD, 'K', '_SHELL', '지울폴더')), 'K 에서 지워졌다');
    const kbin = path.join(LEAD, 'K', '.fakebin');
    assert(!fs.existsSync(kbin) || !fs.readdirSync(kbin).some(n => n.includes('지울폴더')), '휴지통을 거치지 않았다');
    await shot('nobin-deleted', 600);

    // ── 9. 설정: 새 구획 · 팀 설정 내보내기/가져오기
    await page.click('#setBtn');
    await page.waitForSelector('.sheet.show .set-nav');
    for (const s of ['general', 'drives', 'nas', 'projects', 'previews', 'team', 'update']) {
      await page.click(`.set-nav button[data-s="${s}"]`);
      await page.waitForTimeout(650);
      assert(await page.locator(`.set-nav button[data-s="${s}"].on`).count() === 1, '설정 구획 표시 ' + s);
      await shot('settings-' + s, 50);
    }
    assert(/영상백업/.test(await page.locator('.sheet.show .nas-list').innerText()), '설정 NAS 목록');
    assert(await page.locator('.sheet.show .scanlist .sl-r').count() >= 10, '드라이브 스캔 대상 목록');
    const [dl] = await Promise.all([page.waitForEvent('download'), page.click('.sheet.show .td-btns .btn >> nth=0')]);
    const dlPath = path.join(WORK, 'team-export.json');
    await dl.saveAs(dlPath);
    const team = JSON.parse(fs.readFileSync(dlPath, 'utf8'));
    assert(team.format === 'driveboard-team' && dl.suggestedFilename() === 'driveboard-team.json', '팀 설정 내보내기 파일');
    const imp = path.join(WORK, 'team-import.json');
    fs.writeFileSync(imp, JSON.stringify({ format: 'driveboard-team', version: 1, workers: { KMS: '시험작업자' }, client_aliases: { '한결': '한결증권' }, rules: [] }));
    await page.setInputFiles('.sheet.show .td-btns input[type=file]', imp);
    await page.waitForSelector('.sheet.show .tdiff .td-g');
    await page.locator('.sheet.show .tdiff').scrollIntoViewIfNeeded();
    await shot('settings-team-import', 300);
    await page.click('.sheet.show .td-acts .btn.primary');
    await page.waitForSelector('.sheet.show .tdiff .note.lime');
    st = await state();
    assert(st.settings.workers.KMS === '시험작업자' && st.settings.client_aliases['한결'] === '한결증권', '가져오기 적용');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);

    // ── 10. 프로젝트 내비(ui/projects.js 를 붙인다)
    await page.click('.nv[data-nav="projects"]');
    await page.waitForSelector('.proj-host');
    await page.waitForTimeout(1600);
    assert(await page.locator('.nv[data-nav="projects"].on').count() === 1, '내비 「프로젝트」 선택됨');
    assert(await page.locator('#search').isHidden(), '프로젝트 화면에서는 위쪽 파일 검색을 숨긴다');
    await shot('projects', 400);
    // 프로젝트 카드 선택 → 셸 트레이(공유 선택) → 복사 팝오버 → Esc 로 해제가 프로젝트 화면에도 전해진다
    const pck = page.locator('.proj-host .pc-ck');
    if (await pck.count()) {
      await pck.first().click({ force: true });
      await page.waitForSelector('#tray.show', { timeout: 5000 });
      await page.click('#trCopy');
      await page.waitForSelector('.mvpop.copy .mvrow');
      await page.waitForFunction(() => !document.querySelector('.mvpop .bar.loading'), null, { timeout: 10000 });
      await shot('projects-copy-pop', 300);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(200);
      await page.keyboard.press('Escape');
      await page.waitForSelector('#tray:not(.show)', { state: 'attached', timeout: 5000 });   // 숨은 트레이는 visibility:hidden
      await page.waitForTimeout(200);
      assert(await page.locator('.proj-host .sel').count() === 0, 'Esc 로 해제하면 프로젝트 카드 선택도 풀린다');
      log('projects selection ↔ tray ok');
    } else log('프로젝트 카드 체크 상자를 못 찾아 선택 연동은 건너뜀');
    await page.click('.nv[data-nav="storage"]');
    await page.waitForSelector('.hero');

    // ── 11. 1100x720 · 움직임 줄이기
    await page.setViewportSize({ width: 1100, height: 720 });
    await page.waitForTimeout(400);
    await shot('lead-home-1100', 900);
    await page.setViewportSize({ width: 1360, height: 860 });

    // ── 12. 연결 안 된 장치: 서버를 끄고 J 폴더를 치운 뒤 다시 띄운다(같은 데이터 폴더)
    await stopServer(srv);
    fs.renameSync(path.join(LEAD, 'J'), path.join(WORK, 'J_unplugged'));
    srv = await startServer(LEAD, HOME_L);
    log('lead server restarted without J: ' + srv.url);
    await page.goto(srv.url, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.rl[data-l="J"].off', { timeout: 15000 });
    assert(/연결 안 됨/.test(await page.locator('.rl[data-l="J"] .rl-fr').innerText()), '레일에 「연결 안 됨」');
    const offCard = page.locator('.dcard[data-l="J"].off');
    await offCard.scrollIntoViewIfNeeded();
    assert(/연결 안 됨 · 마지막 스캔/.test(await offCard.locator('.dc-foot').innerText()), '회색 카드 문구');
    await shot('lead-offline', 600);
    await page.click('.rl[data-l="J"]');
    await page.waitForSelector('.dv-off:not([hidden])');
    await page.waitForSelector('.crow');
    assert(await page.locator('.dv-head .acts .btn:has-text("다시 스캔")').count() === 0, '연결 안 된 장치는 스캔 버튼이 없다');
    await shot('offline-detail', 900);
    await page.locator('.crow[data-c^="c"]').first().click();
    await page.waitForSelector('.rows .row');
    assert(await page.locator('.rows .row .ck.lock').count() >= 1 && await page.locator('.rows .row button.ck').count() === 0, '기록은 볼 수 있지만 고를 수 없다');
    await shot('offline-list', 600);
    // [기록 지우기]
    await page.click('.nv[data-nav="storage"]');
    await page.waitForSelector('.dcard[data-l="J"].off');
    await page.locator('.dcard[data-l="J"] .dc-foot .btn').click();
    await page.waitForSelector('.sheet.show');
    await shot('forget-sheet', 400);
    await page.click('.sheet.show .sh-f .btn.danger');
    await page.waitForSelector('.rl[data-l="J"]', { state: 'detached', timeout: 10000 });
    log('forgot J');

    // ── 13. 움직임 줄이기
    const ctx2 = await browser.newContext({ viewport: { width: 1360, height: 860 }, reducedMotion: 'reduce' });
    const p2 = await ctx2.newPage();
    watch(p2, 'reduced');
    await p2.goto(srv.url, { waitUntil: 'domcontentloaded' });
    await p2.waitForSelector('.dcard');
    await p2.waitForTimeout(500);
    await p2.screenshot({ path: path.join(SHOTS, 'v2-shell-lead-reduced.png') });
    log('shot v2-shell-lead-reduced.png');
    await ctx2.close();

    // ── 14. 팀원 PC(C+D): 비어 보이지 않는 홈 · 같은 드라이브 복사 = 폴더 고르기(브라우저 모드 → 경로 입력)
    msrv = await startServer(MEMBER, HOME_M);
    log('member server ' + msrv.url);
    const ctx3 = await browser.newContext({ viewport: { width: 1360, height: 860 } });
    const p3 = await ctx3.newPage();
    watch(p3, 'member');
    const shot3 = async (name, settle = 500) => { await p3.waitForTimeout(settle); await p3.screenshot({ path: path.join(SHOTS, `v2-shell-${name}.png`) }); log(`shot v2-shell-${name}.png`); };
    await p3.goto(msrv.url, { waitUntil: 'domcontentloaded' });
    await p3.waitForSelector('.first-run');
    await shot3('member-fresh', 800);
    await p3.click('.first-run .btn.primary');
    await p3.waitForFunction(() => window.DriveBoard.state.drives.every(d => d.scanned), null, { timeout: 60000 });
    await p3.waitForSelector('.dgrid.pair .dcard.big');
    await p3.waitForSelector('.invite');
    await p3.waitForSelector('.rail-quick .rq', { timeout: 10000 });
    assert(await p3.locator('.rl-add').count() === 1, '팀원 레일에도 「NAS 추가」');
    await shot3('member-home', 1200);
    await p3.evaluate(() => document.querySelector('#view').scrollTop = 600);
    await shot3('member-home-scroll', 500);
    await p3.setViewportSize({ width: 1100, height: 720 });
    await p3.evaluate(() => document.querySelector('#view').scrollTop = 0);
    await shot3('member-home-1100', 800);
    await p3.setViewportSize({ width: 1360, height: 860 });
    // C:\_SHELL\팀원폴더 → 같은 C 안의 _SHELL_DEST 로 복사
    await p3.click('.rl[data-l="C"]');
    await p3.waitForSelector('.tabrow .tabs button');
    await p3.locator('.screen:not(.leave) .tabrow .tabs button', { hasText: '폴더' }).click();
    await p3.locator('.screen:not(.leave) .rows .row:has-text("_SHELL")').first().click({ position: { x: 300, y: 20 } });
    await p3.waitForFunction(() => [...document.querySelectorAll('.fcrumbs button.cur')].some(b => b.textContent === '_SHELL'), null, { timeout: 8000 });
    await p3.locator('.rows .row:has-text("팀원폴더") .ck').click();
    await p3.click('#trCopy');
    await p3.waitForSelector('.mvpop.copy .mvrow[data-l="C"]');
    await p3.click('.mvpop .mvrow[data-l="C"]');
    await p3.waitForSelector('.mvpop .mv-path .tin');
    const dest = path.join(MEMBER, 'C', '_SHELL_DEST');
    await p3.fill('.mvpop .mv-path .tin', dest);
    await shot3('member-pick-folder', 300);
    await p3.click('.mvpop .mv-path .btn');
    await p3.waitForSelector('.sheet.show .stats3 .stat .v');
    assert(/고른 폴더에 복사할까요/.test(await p3.locator('.sheet.show h3').innerText()), '고른 폴더 복사 시트');
    await shot3('member-copy-sheet', 500);
    await p3.evaluate(() => document.querySelectorAll('.toast').forEach(t => t.remove()));
    await p3.click('.sheet.show .sh-f .btn.primary');
    await p3.waitForSelector('.toast:has-text("복사했어요")', { timeout: 20000 });
    assert(fs.existsSync(path.join(dest, '팀원폴더', 'a.txt')), '고른 폴더에 복사본');
    assert(fs.existsSync(path.join(MEMBER, 'C', '_SHELL', '팀원폴더', 'a.txt')), '원본은 그대로');
    await shot3('member-copy-done', 500);
    await ctx3.close();
  } catch (e) {
    failed = e;
  } finally {
    await browser.close();
    await stopServer(srv);
    await stopServer(msrv);
    gh.srv.close();
  }
  if (failed) console.error('FAIL:', (failed && failed.stack) || failed);
  if (errors.length) { console.error('콘솔/페이지 오류 ' + errors.length + '건:'); errors.forEach(e => console.error('  ' + e)); }
  if (failed || errors.length) process.exit(1);
  console.log(`OK — ${steps.length} 단계, 오류 0 (작업 폴더 ${WORK})`);
})();
