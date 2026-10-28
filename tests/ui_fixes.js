#!/usr/bin/env node
/* DriveBoard UI 회귀 시험(Playwright, 헤드리스 Chromium) — 실제 서버(app.py --fake, 팀장 구성)로 돈다.
 *
 *   NODE_PATH=/opt/node22/lib/node_modules node tests/ui_fixes.js [작업폴더]
 *
 * 확인하는 것
 *  · 스캔 전 홈: 아직 안 본 공간이 「시스템·확인 불가」 가 아니라 「아직 스캔 안 함」
 *  · 선택이 없을 때 숨은 트레이 버튼이 Tab 순서에 없다
 *  · 「모두 스캔」 위에 포인터를 둔 채 스캔이 끝나면 툴팁도 「취소」 문구에서 바뀐다
 *  · 스캔 뒤 홈: 스캔 안 한 외장·USB 의 공간은 「아직 스캔 안 함」 쪽에 들어간다
 *  · 중복 파일: Ctrl+A 는 묶음마다 사본 하나를 남긴다 · 한 묶음의 사본을 전부 고르면 확인창이 경고하고
 *    Enter 한 번으로는 지나가지 않는다 · 부제는 같은 이름을 장치 꼬리표로 구분한다
 *  · 1100×720 이동 팝오버: 묶음 제목(「외장·USB」)이 첫 줄 없이 홀로 끝에 걸리지 않는다
 *  · NAS 스캔 스위치 토스트의 조사(「영상백업은」) · 드라이브 카드의 숨은 스캔 버튼이 자리를 차지하지 않는다
 *  · 영구 삭제가 확인창의 양(expect)을 보내고, 409 changed 면 늘어난 양 + [다시 스캔](응답은 가로채 지우지 않는다)
 *  · 창 닫기 확인 시트(파일 작업 중): 기본 = 계속 쓰기, [작업을 멈추고 닫기] → 닫는 중
 * 파일은 하나도 지우지 않는다(확인창은 열고 닫기만 한다). 콘솔 오류·페이지 오류·실패한 요청이 있으면 exit 1.
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
const WORK = path.resolve(process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'db-fixes-')));
const LEAD = path.join(WORK, 'lead');
const HOME = path.join(WORK, 'home');

const log = s => console.log('  · ' + s);
function assert(c, msg) { if (!c) throw new Error('확인 실패: ' + msg); log('ok — ' + msg); }

function startServer(fake, home) {
  const port = 8790 + Math.floor(Math.random() * 600) + 900;
  const env = Object.assign({}, process.env, { DRIVEBOARD_HOME: home, DRIVEBOARD_NO_UPDATE_CHECK: '1' });
  delete env.DRIVEBOARD_FAKE;
  const proc = spawn('python3', [path.join(ROOT, 'app.py'), '--no-open', '--fake', fake, '--port', String(port)], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '';
  return new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('server did not start: ' + out)), 30000);
    const on = d => {
      out += d;
      const m = out.match(/DriveBoard: (http:\/\/127\.0\.0\.1:\d+\/)/);
      if (m) { clearTimeout(t); res({ url: m[1], proc }); }
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
  execFileSync('python3', [path.join(ROOT, 'tests', 'make_fake_drives.py'), LEAD, '--scale', 'demo', '--profile', 'lead', '--no-video', '--clean'], { stdio: 'ignore' });
  fs.rmSync(HOME, { recursive: true, force: true });
  const NAS = fs.readdirSync(LEAD).find(n => n.startsWith('N-'));
  const srv = await startServer(LEAD, HOME);
  log('server ' + srv.url + ' (NAS ' + NAS + ')');
  const browser = await chromium.launch();
  const errors = [];
  let failed = null;
  try {
    const ctx = await browser.newContext({ viewport: { width: 1360, height: 860 }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    let allowHttp = 0;   // 일부러 받는 409(가로챈 응답)는 브라우저가 콘솔에 남긴다 — 그만큼만 봐준다
    page.on('console', m => { if (m.type() !== 'error') return; if (allowHttp > 0 && /status of 409/.test(m.text())) { allowHttp--; return; } errors.push('console: ' + m.text()); });
    page.on('pageerror', e => errors.push('pageerror: ' + ((e && e.stack) || e)));
    const destructive = [];
    let probing = false;   // 가로채서 서버에 닿지 않는 요청
    page.on('request', r => { if (/\/api\/ops\/(recycle|delete)/.test(r.url()) && !probing) destructive.push(r.url()); });
    page.on('requestfailed', r => { if (!r.url().endsWith('favicon.ico') && !/\/media\//.test(r.url())) errors.push('requestfailed: ' + r.url()); });
    const shot = async (name, settle = 400) => { await page.waitForTimeout(settle); await page.screenshot({ path: path.join(SHOTS, `fixes-${name}.png`) }); log(`shot fixes-${name}.png`); };
    const state = () => page.evaluate(() => window.DriveBoard.state);
    const waitIdle = (ms = 90000) => page.waitForFunction(() => {
      const S = window.DriveBoard.state;
      return !S.busy && !Object.values(S.scans || {}).some(s => s.phase === 'scan' || s.phase === 'finalize');
    }, null, { timeout: ms });
    const legendChip = k => page.locator(`.screen:not(.leave) .lg[data-c="${k}"]`).first();

    // ── 1. 스캔 전 홈: 안 본 공간은 「아직 스캔 안 함」
    await page.goto(srv.url, { waitUntil: 'domcontentloaded' });
    log('visibilityState=' + await page.evaluate(() => document.visibilityState));
    await page.waitForSelector('.first-run', { timeout: 15000 });
    await page.waitForTimeout(600);
    assert(await legendChip('ns').isVisible(), '스캔 전 홈 범례에 「아직 스캔 안 함」');
    assert(/아직 스캔 안 함/.test(await legendChip('ns').innerText()), '칩 이름: ' + (await legendChip('ns').innerText()).replace(/\s+/g, ' '));
    assert(!(await legendChip('un').isVisible()), '스캔 전에는 「시스템·확인 불가」 칩이 없다');
    assert(/스캔하면/.test(await legendChip('ns').getAttribute('data-tip')), 'ns 칩 툴팁');
    await shot('home-before-scan');

    // ── 2. 선택이 없으면 트레이는 Tab 순서에서 빠진다
    assert(await page.evaluate(() => document.querySelector('#tray').inert === true), '숨은 트레이는 inert');
    let inTray = 0;
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    for (let i = 0; i < 70; i++) {
      await page.keyboard.press('Tab');
      if (await page.evaluate(() => !!(document.activeElement && document.activeElement.closest('#tray')))) inTray++;
    }
    assert(inTray === 0, `Tab 70번 동안 트레이 버튼에 포커스 ${inTray}번`);

    // ── 3. 「모두 스캔」 위에 포인터를 둔 채 스캔 → 끝나면 툴팁도 바뀐다
    await page.hover('#scanBtn');
    await page.waitForSelector('#tip.show', { timeout: 3000 });
    await page.click('#scanBtn');
    await page.waitForFunction(() => /취소/.test(document.querySelector('#tip').textContent), null, { timeout: 5000 }).catch(() => log('스캔이 너무 빨라 「취소」 툴팁을 못 봤어요'));
    await page.waitForFunction(() => window.DriveBoard.state.drives.filter(d => d.scan).every(d => d.scanned), null, { timeout: 120000 });
    await waitIdle();
    await page.waitForTimeout(700);
    const tipNow = await page.evaluate(() => ({ show: document.querySelector('#tip').classList.contains('show'), t: document.querySelector('#tip').textContent, want: document.querySelector('#scanBtn').getAttribute('data-tip'), btn: document.querySelector('#scanBtn').innerText }));
    log('tip: ' + JSON.stringify(tipNow));
    assert(!tipNow.show || tipNow.t === tipNow.want, '스캔 뒤 떠 있는 툴팁이 버튼의 지금 문구와 같다');
    assert(!(tipNow.show && /취소/.test(tipNow.t) && !/취소/.test(tipNow.btn)), '「모두 스캔」 위에 「취소」 툴팁이 남지 않는다');
    await page.mouse.move(5, 5);

    // ── 4. 스캔 뒤 홈: 스캔 안 한 외장·USB 공간은 「아직 스캔 안 함」
    const st = await state();
    const unscanned = st.drives.filter(d => !d.scanned && d.used > 0 && d.connected !== false);
    log('스캔 안 한 장치: ' + unscanned.map(d => d.letter).join(','));
    assert(unscanned.length >= 1, '모두 스캔은 외장·USB 를 건너뛴다');
    assert(await legendChip('ns').isVisible(), '스캔 뒤에도 안 본 장치 몫은 「아직 스캔 안 함」');
    await shot('home-after-scan', 800);

    // ── 5. 중복 파일
    const dupRec = page.locator('.rec', { hasText: '중복' });
    await page.waitForSelector('.rec', { timeout: 10000 });
    assert(await dupRec.count() > 0, '중복 추천 카드');
    await dupRec.first().click();
    await page.waitForSelector('.dg .row', { timeout: 15000 });
    await page.waitForTimeout(500);
    await page.mouse.click(700, 120);   // 화면에 초점(입력칸 밖)
    await page.keyboard.press('Control+a');
    await page.waitForTimeout(300);
    const sel = await page.evaluate(() => [...document.querySelectorAll('.dg')].map(g => { const r = g.querySelectorAll('.row'); return { n: r.length, s: [...r].filter(x => x.classList.contains('sel')).length, diff: !!g.querySelector('.vbadge.no') }; }));
    const nSel = sel.reduce((a, g) => a + g.s, 0);
    log(`Ctrl+A: 묶음 ${sel.length}개 · 고름 ${nSel}개`);
    assert(nSel > 0, 'Ctrl+A 가 사본을 고른다');
    assert(sel.every(g => g.s < g.n), 'Ctrl+A 뒤 모든 묶음에 남는 사본이 있다');
    assert(sel.filter(g => !g.diff).every(g => g.s === g.n - 1), '묶음마다 정확히 하나씩 남긴다');
    assert(await page.locator('.toast:has-text("하나씩 남기고")').count() === 1, 'Ctrl+A 안내 토스트');
    await shot('dupes-ctrl-a', 300);
    // Ctrl+A → Delete: 남는 사본이 있으니 경고 없음
    await page.keyboard.press('Delete');
    await page.waitForSelector('.sheet.show .stats3', { timeout: 10000 });
    assert(await page.locator('.sheet.show .note.danger:has-text("사본을 모두"), .sheet.show .note.danger:has-text("모든 사본")').count() === 0, 'Ctrl+A 선택에는 「모든 사본」 경고가 없다');
    await page.keyboard.press('Escape');
    await page.waitForSelector('.sheet', { state: 'detached', timeout: 5000 }).catch(() => { });
    await page.keyboard.press('Escape');   // 선택 해제
    await page.waitForTimeout(300);
    // 한 묶음의 두 사본을 모두 고르고 Delete → Enter
    const g0 = page.locator('.dg').first();
    const rows0 = g0.locator('.row');
    const n0 = await rows0.count();
    for (let i = 0; i < n0; i++) await rows0.nth(i).click({ position: { x: 300, y: 18 } });
    assert(await g0.locator('.row.sel').count() === n0, `첫 묶음의 사본 ${n0}개를 모두 골랐다`);
    await page.keyboard.press('Delete');
    await page.waitForSelector('.sheet.show .stats3', { timeout: 10000 });
    await page.waitForTimeout(250);
    const warn = page.locator('.sheet.show .note.danger', { hasText: '남길 사본이 없어요' });
    assert(await warn.count() === 1, '모든 사본을 고르면 확인창이 경고한다');
    assert(await page.locator('.sheet.show .ckrow').count() === 1, '따로 확인하는 체크 줄');
    const goBtn = page.locator('.sheet.show .sh-f .btn.danger, .sheet.show .sh-f .btn.solid-danger').first();
    assert(await goBtn.isDisabled(), '확인 전에는 실행 버튼이 꺼져 있다');
    assert(!(await page.evaluate(() => document.activeElement && /danger/.test(document.activeElement.className) && !!document.activeElement.closest('.sh-f'))), '실행 버튼에 기본 포커스가 없다');
    const sub = await page.locator('.sheet.show .sh-h p').innerText();
    log('부제: ' + sub);
    const nm = await page.locator('.dg').first().locator('.dg-h .n').innerText();
    assert(!sub.startsWith(`${nm}, ${nm}`), '부제가 같은 이름 둘을 그대로 늘어놓지 않는다');
    await shot('dupes-all-copies', 200);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(400);
    assert(destructive.length === 0, 'Enter 한 번으로는 지나가지 않는다(포커스가 닫기에 있어 닫히기만 한다)');
    if (!(await page.locator('.sheet.show').count())) {
      await page.keyboard.press('Delete');
      await page.waitForSelector('.sheet.show .ckrow', { timeout: 10000 });
    }
    await page.click('.sheet.show .ckrow');
    assert(!(await goBtn.isDisabled()), '체크하면 실행 버튼이 켜진다');
    await page.keyboard.press('Escape');
    await page.waitForSelector('.sheet', { state: 'detached', timeout: 5000 }).catch(() => { });

    // ── 6. 1100×720 이동 팝오버: 묶음 제목이 첫 줄 없이 끝에 걸리지 않는다
    await page.setViewportSize({ width: 1100, height: 720 });
    await page.waitForTimeout(500);
    for (const mode of ['move', 'copy']) {
      await page.click(mode === 'move' ? '#trMove' : '#trCopy');
      await page.waitForSelector(`.mvpop.${mode} .mvrow`, { timeout: 8000 });
      await page.waitForFunction(() => !document.querySelector('.mvpop .bar.loading'), null, { timeout: 15000 });
      await page.waitForTimeout(400);
      const orphan = await page.evaluate(() => {
        const list = document.querySelector('.mvpop .mv-list');
        const lr = list.getBoundingClientRect();
        const out = [];
        for (const gh of list.querySelectorAll('.mv-grp')) {
          const r = gh.getBoundingClientRect();
          if (r.top >= lr.bottom - 1) break;
          const nx = gh.nextElementSibling;
          const nr = nx.getBoundingClientRect();
          if (nr.top + Math.min(nr.height, 30) > lr.bottom) out.push(gh.textContent);
        }
        return { out, sh: list.scrollHeight, ch: list.clientHeight, sb: getComputedStyle(document.querySelector('.mvpop .sb')).opacity, grpColor: getComputedStyle(list.querySelector('.mv-grp') || list).color };
      });
      log(`${mode} popover: ${JSON.stringify(orphan)}`);
      assert(orphan.out.length === 0, `${mode}: 첫 줄 없이 끝에 걸린 묶음 제목이 없다`);
      assert(orphan.grpColor !== 'rgb(76, 79, 87)', `${mode}: 묶음 제목 글자색이 --text4 가 아니다`);
      await shot(`move-pop-1100-${mode}`, 100);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);
    }
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 1360, height: 860 });

    // ── 7. NAS 추가 → 카드 이름 · 스캔 스위치 토스트의 조사
    await page.click('.nv[data-nav="storage"]').catch(() => { });
    await page.waitForTimeout(500);
    await page.click('.rail-label[data-g="net"] .ibtn');
    await page.waitForSelector('.sheet.show .nas-add');
    await page.fill('.sheet.show .nas-add .addrow .tin', 'ORICO');
    await page.click('.sheet.show .nas-add .addrow .btn');
    await page.waitForSelector('.sheet.show .shrow');
    await page.click('.sheet.show .sh-f .btn.primary');
    await page.waitForSelector(`.rl[data-l="${NAS}"]`, { timeout: 10000 });
    await page.waitForSelector('.sheet', { state: 'detached', timeout: 5000 }).catch(() => { });
    await page.waitForTimeout(600);
    const nCard = page.locator(`.dcard[data-l="${NAS}"]`);
    await nCard.scrollIntoViewIfNeeded();
    await page.mouse.move(5, 5);
    await page.waitForTimeout(300);
    const nmBox = await nCard.evaluate(el => { const n = el.querySelector('.dc-nm'); const b = el.querySelector('.scanbtn'); return { cut: n.scrollWidth > n.clientWidth + 1, w: b.getBoundingClientRect().width, text: n.textContent }; });
    log('NAS 카드: ' + JSON.stringify(nmBox));
    assert(nmBox.w < 1, '호버 전 숨은 스캔 버튼은 자리를 차지하지 않는다');
    assert(!nmBox.cut, 'NAS 카드 이름이 잘리지 않는다: ' + nmBox.text);
    const sw = nCard.locator('.sw');
    if (await sw.count()) {
      const toastOf = async word => { const sel = `.toast:has-text("${word}")`; await page.waitForSelector(sel, { timeout: 5000 }); const t = await page.locator(sel).last().innerText(); await page.evaluate(() => document.querySelectorAll('.toast').forEach(x => x.remove())); return t; };
      await page.evaluate(() => document.querySelectorAll('.toast').forEach(x => x.remove()));
      await sw.click();
      const on = await toastOf('넣었어요');   // 카드 스위치는 「넣기」 전용 — 빼기는 설정의 스캔 대상 목록에서
      log('토스트: ' + on.replace(/\s+/g, ' '));
      await page.click('#setBtn');
      await page.waitForSelector('.sheet.show .set-sec');
      const ssw = page.locator('.sheet.show .sw[aria-label*="영상백업"]');
      await ssw.scrollIntoViewIfNeeded();
      await ssw.click();
      const off = await toastOf('뺐어요');
      await page.keyboard.press('Escape');
      await page.waitForSelector('.sheet', { state: 'detached', timeout: 5000 }).catch(() => { });
      log('토스트: ' + off.replace(/\s+/g, ' '));
      assert(/영상백업은 「모두 스캔」에서 뺐어요/.test(off), '받침 있는 이름 뒤에 「은」');
    } else log('NAS 카드에 스캔 스위치가 없어 조사 확인은 건너뜀');

    // ── 8. 아직 스캔 안 한 NAS 화면: 4.66TB 가 「시스템·확인 불가」 로 보이지 않는다
    await page.click(`.rl[data-l="${NAS}"]`);
    await page.waitForTimeout(900);
    assert(await legendChip('ns').isVisible() && !(await legendChip('un').isVisible()), '스캔 안 한 NAS 화면 범례는 「아직 스캔 안 함」 하나');
    await shot('nas-unscanned', 300);

    // ── 9. 영구 삭제: 확인창이 본 양을 보내고, 서버가 「늘었어요」(409 changed)로 거절하면 무엇이 늘었는지 + 다시 스캔
    await page.evaluate(() => fetch('/api/scan', { method: 'POST', headers: { 'X-DB-Token': document.querySelector('meta[name="db-token"]').content, 'Content-Type': 'application/json' }, body: JSON.stringify({ drives: ['K'] }) }));
    await page.waitForFunction(() => { const d = window.DriveBoard.state.drives.find(x => x.letter === 'K'); return d && d.scanned; }, null, { timeout: 60000 });
    await waitIdle();
    await page.click('.rl[data-l="K"]');
    await page.waitForTimeout(700);
    await page.locator('.screen:not(.leave) .tabs button', { hasText: '폴더' }).first().click();
    await page.waitForSelector('.screen:not(.leave) .row .ck', { timeout: 15000 });
    await page.locator('.screen:not(.leave) .row .ck').first().click();
    await page.keyboard.press('Delete');
    await page.waitForSelector('.sheet.show.danger .ckrow', { timeout: 10000 });
    await page.waitForTimeout(250);
    let sent = null;
    await page.route('**/api/ops/delete', async r => {
      sent = JSON.parse(r.request().postData() || '{}');
      const p0 = (sent.paths || [])[0] || '';
      await r.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'changed', message: '스캔한 뒤 내용이 늘었어요(또는 스캔에서 뺀 폴더가 들어 있어요). 다시 스캔해서 확인한 뒤 지워 주세요.', paths: [p0], grown: [{ path: p0, bytes: 9 << 20, files: 12, confirmed_bytes: 4 << 20, confirmed_files: 7 }] }) });
    });
    probing = true; allowHttp++;
    await page.click('.sheet.show .ckrow');
    await page.click('.sheet.show .sh-f .btn.solid-danger:not([disabled])');
    await page.waitForSelector('.sheet.show .note.danger:has-text("늘어서 지우지 않았어요")', { timeout: 8000 });
    probing = false;
    assert(sent && sent.confirm === true && Array.isArray(sent.expect) && sent.expect.length === sent.paths.length && sent.expect.every(e => e.bytes > 0 && e.files > 0), '영구 삭제 요청이 확인창에 보인 양(expect)을 함께 보낸다');
    const gn = await page.locator('.sheet.show .note.danger:has-text("늘어서")').innerText();
    log('늘어난 양: ' + gn.replace(/\s+/g, ' '));
    assert(/MB → 9/.test(gn) && /7 → 12/.test(gn), '무엇이 얼마나 늘었는지 보여 준다');
    assert(await page.locator('.sheet.show .note.danger .btn:has-text("다시 스캔")').count() === 1, '[다시 스캔] 버튼');
    assert(await page.locator('.sheet.show .sh-f .btn.solid-danger').isDisabled(), '거절된 뒤 실행 버튼이 꺼진다');
    await shot('delete-grown', 200);
    await page.unroute('**/api/ops/delete');
    await page.keyboard.press('Escape');
    await page.waitForSelector('.sheet', { state: 'detached', timeout: 5000 }).catch(() => { });
    await page.keyboard.press('Escape');

    // ── 10. 창 닫기: 파일 작업 중이면 먼저 묻는다(기본 = 계속 쓰기). 원본 삭제 단계면 「마저 지운 뒤」
    const closeReq = [];
    await page.route('**/api/app/close', async r => { closeReq.push(r.request().url()); await r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true,"deleting":true}' }); });
    const ask = st => page.evaluate(s => window.DriveBoard.askClose(s), st);
    const busy = { busy: true, deleting: true, grace_s: 120, jobs: [{ id: 'j1', type: 'move', status: 'running', title: '이동 · 260803_소담코스 → J:' }, { id: 'j2', type: 'delete', status: 'queued', title: '영구 삭제 · 옛 렌더' }] };
    assert(await ask({ busy: false, jobs: [] }) === false, '작업이 없으면 묻지 않는다');
    assert(await ask(busy) === true, '작업 중이면 묻는다');
    await page.waitForSelector('.sheet.show .closejobs .cj', { timeout: 5000 });
    assert(await page.locator('.sheet.show .closejobs .cj').count() === 2, '진행 중인 작업 목록');
    assert(await page.locator('.sheet.show .note:has-text("마저 지운 뒤")').count() === 1, '원본 삭제 단계 안내(마저 지운 뒤 닫힘)');
    await page.waitForTimeout(150);
    assert(await page.evaluate(() => (document.activeElement || {}).textContent) === '계속 쓰기', '기본 포커스는 [계속 쓰기]');
    await shot('close-ask', 200);
    await page.keyboard.press('Enter');
    await page.waitForSelector('.sheet', { state: 'detached', timeout: 5000 });
    assert(closeReq.length === 0, 'Enter 는 계속 쓰기 — 닫기 요청 없음');
    await ask(busy);
    await page.waitForSelector('.sheet.show .closejobs');
    await page.click('.sheet.show .sh-f .btn.solid-danger');
    await page.waitForSelector('.sheet.show h3:has-text("닫는 중이에요")', { timeout: 5000 });
    assert(closeReq.length === 1, '[작업을 멈추고 닫기] 가 닫기를 요청한다');
    assert(/마저 지운 뒤/.test(await page.locator('.sheet.show .sh-h p').innerText()), '닫는 중 — 지우던 항목을 마저 지운다는 안내');
    await page.keyboard.press('Escape');
    assert(await page.locator('.sheet.show').count() === 1, '닫는 중에는 Esc 로 시트가 닫히지 않는다');
    await page.unroute('**/api/app/close');
    assert(destructive.length === 0, '시험 중 휴지통·삭제 요청을 보내지 않았다');
  } catch (e) {
    failed = e;
  } finally {
    await browser.close();
    await stopServer(srv);
  }
  if (errors.length) { console.log('브라우저 오류:'); errors.forEach(e => console.log('  ✗ ' + e)); }
  if (failed) { console.log('실패: ' + (failed.stack || failed)); process.exit(1); }
  if (errors.length) process.exit(1);
  console.log('ui_fixes: 모두 통과');
})();
