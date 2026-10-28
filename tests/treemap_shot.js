// 트리맵 데모를 헤드리스 Chromium 으로 띄워 흐름을 돌리고 스크린샷을 남긴다. 콘솔 오류가 하나라도 있으면 실패.
// 실행: NODE_PATH=/opt/node22/lib/node_modules node tests/treemap_shot.js
'use strict';
const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, '_shots');
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css', '.woff2': 'font/woff2', '.txt': 'text/plain; charset=utf-8' };

function serve() {
  const srv = http.createServer((req, res) => {
    const u = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const f = path.normalize(path.join(ROOT, u));
    if (!f.startsWith(ROOT + path.sep) || !/^\/(ui|tests)\//.test(u) || !TYPES[path.extname(f)]) { res.writeHead(404); res.end(); return; }
    fs.readFile(f, (err, buf) => {
      if (err) { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(f)] });
      res.end(buf);
    });
  });
  return new Promise(r => srv.listen(0, '127.0.0.1', () => r(srv)));
}

const failures = [];
function check(ok, what) { if (!ok) { failures.push(what); console.log('  ✗ ' + what); } else console.log('  ✓ ' + what); }

async function frames(page, n = 2) {
  await page.evaluate(n => new Promise(r => { const f = k => k ? requestAnimationFrame(() => f(k - 1)) : r(); f(n); }), n);
}
async function state(page) { return page.$eval('.tm-root', el => el.dataset.state); }
async function waitReady(page, timeout = 8000) {
  await page.waitForFunction(() => { const r = document.querySelector('.tm-root'); return r && (r.dataset.state === 'ready' || r.dataset.state === 'empty'); }, null, { timeout });
  await frames(page, 2);
}
async function stageBox(page) { return page.$eval('.tm-root', el => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; }); }

// 격자로 훑어 원하는 종류의 호버 지점을 찾는다(데모는 시드 고정이라 결정적)
async function probe(page, want, opts = {}) {
  const b = await stageBox(page);
  const step = opts.step || 22;
  const y0 = opts.y0 || 4, x0 = opts.x0 || 4;
  for (let y = b.y + y0; y < b.y + b.h - 2; y += step) {
    for (let x = b.x + x0; x < b.x + b.w - 2; x += step) {
      await page.mouse.move(x, y);
      await frames(page, 1);
      const k = await page.$eval('.tm-root', el => el.dataset.hover || '');
      if (want.includes(k)) return { x, y, kind: k };
    }
  }
  return null;
}

async function run() {
  fs.mkdirSync(OUT, { recursive: true });
  const srv = await serve();
  const base = 'http://127.0.0.1:' + srv.address().port + '/tests/treemap_demo.html';
  const browser = await chromium.launch();
  const errors = [];
  async function newPage(opts = {}) {
    const ctx = await browser.newContext({ viewport: { width: 1100, height: 640 }, deviceScaleFactor: opts.dpr || 1, reducedMotion: opts.reduced ? 'reduce' : 'no-preference' });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') errors.push('[console] ' + m.text()); });
    page.on('pageerror', e => errors.push('[pageerror] ' + e.message));
    page.on('requestfailed', r => errors.push('[request] ' + r.url() + ' ' + (r.failure() && r.failure().errorText)));
    return page;
  }
  const shot = (page, name) => page.screenshot({ path: path.join(OUT, 'treemap-' + name + '.png') });

  try {
    // ---------------------------------------------------------- 기본 흐름
    console.log('기본 흐름');
    let page = await newPage();
    await page.goto(base + '?latency=220');
    await page.waitForFunction(() => document.querySelector('.tm-root') && document.querySelector('.tm-root').dataset.state === 'loading');
    await shot(page, 'loading');
    await waitReady(page);
    await page.evaluate(() => document.fonts.ready);
    await frames(page, 3);
    check((await page.evaluate(() => document.fonts.check('600 12px "Pretendard Variable"'))), 'Pretendard 글꼴 로드');
    check((await page.evaluate(() => [...document.fonts].some(f => f.family.replace(/"/g, '') === 'TM Tabular' && f.status === 'loaded'))), '표 숫자(tnum) 별칭 글꼴 로드');
    await page.mouse.move(2, 2);
    await shot(page, 'initial');
    const rects0 = +(await page.$eval('.tm-root', el => el.dataset.rects));
    check(rects0 > 40, '초기 사각형 수 ' + rects0);
    check((await page.evaluate(() => window.demo.crumbs.length)) === 1, '초기 crumbs 1개');

    // 호버: 그룹 안의 폴더(잎) → 툴팁 + 구성 막대
    let hp = await probe(page, ['dir'], { y0: 30 });
    check(!!hp, '그룹 안 폴더 잎 호버 지점');
    if (hp) {
      await page.mouse.move(hp.x + 1, hp.y + 1);
      await frames(page, 3);
      const tip = await page.$eval('.tm-tip', el => ({ show: el.classList.contains('show'), name: el.querySelector('.tn').textContent, bar: el.querySelectorAll('.tb i').length, parts: [...el.querySelectorAll('.td span')].map(s => s.textContent) }));
      check(tip.show && tip.name.length > 0, '툴팁 표시: ' + tip.name);
      check(tip.bar >= 1, '툴팁 구성 막대 ' + tip.bar + '칸');
      // 구성 줄에 카테고리 이름이 붙는다(색 점만으로는 못 읽는다) · 다른 몫이 있으면 맨 위가 100% 가 아니다
      const names = ['캐시·임시', '렌더·출력', '촬영·소스', '프록시', '기타 영상', '프로젝트', '이미지·시퀀스', '오디오', '압축·백업', '앱·시스템', '기타'];
      check(tip.parts.length >= 1 && tip.parts.every(t => names.some(n => t.startsWith(n + ' '))), '툴팁 구성에 카테고리 이름: ' + tip.parts.join(' | '));
      check(!(tip.parts.length > 1 && / 100%$/.test(tip.parts[0])), '다른 몫이 있으면 맨 위가 100% 로 보이지 않음');
      await page.waitForTimeout(160);
      await shot(page, 'hover');
    }
    // 파일 호버 + 클릭 → onOpenItem
    const fp = await probe(page, ['file', 'seq'], { y0: 10, step: 18 });
    check(!!fp, '파일 호버 지점');
    if (fp) {
      await page.mouse.click(fp.x, fp.y);
      await frames(page, 2);
      const ev = await page.evaluate(() => window.demo.events.slice(-1)[0]);
      check(ev && ev[0] === 'open', 'onOpenItem 호출: ' + (ev && ev[1]));
      await page.mouse.click(fp.x, fp.y, { button: 'right' });
      await frames(page, 2);
      const ev2 = await page.evaluate(() => window.demo.events.slice(-1)[0]);
      check(ev2 && ev2[0] === 'ctx' && Math.abs(ev2[2] - fp.x) < 1.5, 'onContext 호출(좌표 포함)');
    }
    // 드릴: 첫 그룹(가장 큰 = Projects)의 머리 띠 클릭
    const b = await stageBox(page);
    await page.mouse.move(b.x + 40, b.y + 10);
    await frames(page, 2);
    check((await page.$eval('.tm-root', el => el.dataset.hover)) === 'group', '첫 그룹 머리 띠 호버');
    await page.mouse.click(b.x + 40, b.y + 10);
    await page.waitForTimeout(150);
    await shot(page, 'anim-in');
    await waitReady(page);
    await page.mouse.move(2, 2);
    await frames(page, 2);
    await shot(page, 'drill');
    const cr = await page.evaluate(() => window.demo.crumbs.map(c => c.name));
    check(cr.length === 2 && cr[1] === 'Projects', '드릴 후 crumbs: ' + cr.join(' › '));

    // 드릴 화면에서 호버(파일 잎)
    hp = await probe(page, ['file', 'seq'], { y0: 30, step: 26 });
    if (hp) { await page.waitForTimeout(140); await shot(page, 'drill-hover'); }

    // 하이라이트: 범례 칩 '렌더·출력'
    const chip = await page.$('.chip[data-cat="1"]');
    await chip.hover();
    await page.waitForTimeout(320);
    await shot(page, 'highlight');
    await page.mouse.move(2, 2);
    await page.waitForTimeout(300);

    // 위로
    const t0 = Date.now();
    await page.click('#back');
    await page.waitForTimeout(160);
    await shot(page, 'anim-out');
    await waitReady(page);
    console.log('  위로 걸린 시간 ' + (Date.now() - t0) + 'ms');
    await page.mouse.move(2, 2);
    await frames(page, 2);
    await shot(page, 'up');
    check((await page.evaluate(() => window.demo.crumbs.length)) === 1, '위로 후 crumbs 1개');

    // 두 단계 드릴(그룹 안 폴더 잎 클릭) → crumbs 3개
    hp = await probe(page, ['dir'], { y0: 30 });
    if (hp) {
      await page.mouse.click(hp.x, hp.y);
      await waitReady(page);
      const c3 = await page.evaluate(() => window.demo.crumbs.length);
      check(c3 === 3, '그룹 안 폴더 클릭 → crumbs 3개 (' + c3 + ')');
      await page.mouse.move(2, 2);
      await frames(page, 2);
      await shot(page, 'drill2');
      // 'loose'(작은 파일 묶음)는 클릭·우클릭이 아무것도 부르지 않아야 한다(데이터 안전)
      {
        await page.evaluate(() => { window.demo.events.length = 0; });
        const lp = await probe(page, ['loose'], { step: 9 });
        if (lp) {
          await page.mouse.click(lp.x, lp.y);
          await page.mouse.click(lp.x, lp.y, { button: 'right' });
          await frames(page, 2);
          const n = await page.evaluate(() => window.demo.events.length);
          const st = await state(page);
          check(n === 0 && st === 'ready', 'loose 클릭·우클릭 무반응');
        } else check(false, 'loose 칸을 찾지 못함');
      }

      // crumb 클릭으로 루트 복귀(open)
      await page.click('.crumbs button:first-child');
      await waitReady(page);
      check((await page.evaluate(() => window.demo.crumbs.length)) === 1, 'crumb 클릭 → 루트');
    }

    // 빈 폴더
    await page.evaluate(() => window.demo.tm.open(window.demo.emptyId));
    await waitReady(page);
    check((await state(page)) === 'empty', '빈 폴더 상태');
    await shot(page, 'empty');
    await page.evaluate(() => window.demo.tm.open(0));
    await waitReady(page);

    // 크기 변경(ResizeObserver)
    await page.setViewportSize({ width: 820, height: 520 });
    await page.waitForTimeout(120);
    await frames(page, 3);
    const w2 = await page.$eval('.tm-root canvas', c => c.width);
    const r2 = await stageBox(page);
    check(Math.abs(w2 - Math.round(r2.w)) <= 1, '리사이즈 후 캔버스 폭 ' + w2 + ' = ' + Math.round(r2.w));
    await shot(page, 'resized');

    // 경쟁 상황: 확대 도중 크기 변경 → 끝나면 새 크기로 정상 장면
    {
      const b3 = await stageBox(page);
      await page.mouse.click(b3.x + 40, b3.y + 10);
      await page.waitForTimeout(90);
      await page.setViewportSize({ width: 1100, height: 640 });
      await waitReady(page);
      await frames(page, 3);
      const cw3 = await page.$eval('.tm-root canvas', c => c.width), b4 = await stageBox(page);
      const n3 = +(await page.$eval('.tm-root', el => el.dataset.rects));
      check(Math.abs(cw3 - Math.round(b4.w)) <= 1 && n3 > 10, '확대 도중 리사이즈 → 정상 장면(' + n3 + '칸)');
      await page.mouse.move(2, 2);
      await frames(page, 2);
      await shot(page, 'resize-mid-zoom');
      await page.evaluate(() => window.demo.tm.open(0));
      await waitReady(page);
    }
    // 경쟁 상황: 더블클릭은 한 단계만 들어간다
    {
      const b5 = await stageBox(page);
      await page.mouse.dblclick(b5.x + 40, b5.y + 10);
      await waitReady(page);
      await page.waitForTimeout(150);
      await waitReady(page);
      const c5 = await page.evaluate(() => window.demo.crumbs.length);
      check(c5 === 2, '더블클릭 → 한 단계만 (' + c5 + ')');
      await page.evaluate(() => window.demo.tm.open(0));
      await waitReady(page);
    }
    // 경쟁 상황: 확대 도중 open(루트) 가 앞선 탐색을 대신한다
    {
      const b6 = await stageBox(page);
      await page.mouse.click(b6.x + 40, b6.y + 10);
      await page.waitForTimeout(60);
      await page.evaluate(() => window.demo.tm.open(0));
      await waitReady(page);
      await page.waitForTimeout(700);
      await waitReady(page);
      const c6 = await page.evaluate(() => window.demo.crumbs.map(c => c.name).join(' › '));
      check(c6 === 'D:\\', '확대 도중 open(0) → 루트에 머문다 (' + c6 + ')');
    }
    // 위로 갈 곳이 없으면 false
    check((await page.evaluate(() => window.demo.tm.up())) === false, '루트에서 up() → false');
    await page.close();

    // ---------------------------------------------------------- 오류 경로
    console.log('오류 경로');
    page = await newPage();
    await page.goto(base + '?latency=60&errors=1');
    await waitReady(page);
    await page.evaluate(() => window.demo.tm.open(window.demo.errId));
    await page.waitForTimeout(500);
    await waitReady(page);
    check((await page.evaluate(() => window.demo.crumbs.length)) === 1, '로드 실패 시 crumbs 복구');
    check(await page.$eval('.tm-toast', el => el.classList.contains('show')), '실패 안내 토스트');
    await shot(page, 'error-toast');
    await page.close();
    page = await newPage();
    await page.goto(base + '?latency=60&errors=1');
    await page.evaluate(() => { const d = document.getElementById('stage'); d.innerHTML = ''; window.demo.tm.destroy(); window.demo.tm2 = Treemap.create(d, { load: () => Promise.reject(new Error('스캔한 적 없는 드라이브예요')), colorOf: i => 'var(--c' + i + ')', formatSize: x => x + 'B' }); return window.demo.tm2.open(5); });
    await page.waitForFunction(() => document.querySelector('.tm-root').dataset.state === 'error');
    check(await page.$eval('.tm-msg', el => el.classList.contains('show') && el.textContent.includes('스캔한 적 없는')), '첫 로드 실패 → 오류 화면');
    await shot(page, 'error');
    check((await page.$$('.tm-tip')).length === 1, 'destroy 후 툴팁 1개만 남음');
    // 이름은 디스크에서 온다 — 툴팁에 HTML 로 해석되면 안 된다
    await page.evaluate(() => {
      window.demo.tm2.destroy();
      const d = document.getElementById('stage');
      window.__evil = '<img src=x onerror="window.__pwned=1"><b>굵게</b>.mov';
      window.demo.tm3 = Treemap.create(d, {
        load: () => Promise.resolve({ id: 1, kind: 'dir', name: 'X', path: 'X:\\', size: 100, cat_bytes: [0, 100, 0, 0, 0, 0, 0, 0, 0, 0, 0], children: [
          { id: 2, kind: 'file', name: window.__evil, path: 'X:\\' + window.__evil, size: 100, cat: 1 }] }),
        colorOf: i => 'var(--c' + i + ')', formatSize: x => x + 'B'
      });
      return window.demo.tm3.open(1);
    });
    await waitReady(page);
    const sb0 = await stageBox(page);
    await page.mouse.move(sb0.x + sb0.w / 2, sb0.y + sb0.h / 2);
    await frames(page, 3);
    const tipTxt = await page.$eval('.tm-tip .tn', el => ({ t: el.textContent, kids: el.children.length }));
    check(tipTxt.t === (await page.evaluate(() => window.__evil)) && tipTxt.kids === 0 && !(await page.evaluate(() => window.__pwned)), '파일 이름은 글자 그대로(HTML 해석 없음)');
    await page.close();

    // ---------------------------------------------------------- 움직임 줄이기
    console.log('움직임 줄이기');
    page = await newPage({ reduced: true });
    await page.goto(base + '?latency=0');
    await waitReady(page);
    const bb = await stageBox(page);
    const t1 = Date.now();
    await page.mouse.click(bb.x + 40, bb.y + 10);
    await waitReady(page);
    const dt = Date.now() - t1;
    check(dt < 300, 'reduced-motion 드릴 ' + dt + 'ms (애니메이션 없음)');
    await page.close();

    // ---------------------------------------------------------- DPR 2
    console.log('DPR 2');
    page = await newPage({ dpr: 2 });
    await page.goto(base + '?latency=0');
    await waitReady(page);
    await page.evaluate(() => document.fonts.ready);
    await frames(page, 3);
    const cw = await page.$eval('.tm-root canvas', c => c.width);
    const bw = await stageBox(page);
    check(Math.abs(cw - Math.round(bw.w * 2)) <= 1, 'DPR2 캔버스 폭 ' + cw);
    await page.mouse.move(2, 2);
    await page.screenshot({ path: path.join(OUT, 'treemap-dpr2.png'), clip: { x: 16, y: 80, width: 560, height: 330 } });
    await page.close();

    // ---------------------------------------------------------- 부하(2,000+ 사각형) + 호버 60fps
    console.log('부하');
    page = await newPage();
    await page.goto(base + '?latency=0&stress=1');
    await waitReady(page);
    await page.evaluate(() => document.fonts.ready);
    await frames(page, 3);
    const rects = +(await page.$eval('.tm-root', el => el.dataset.rects));
    const rms = +(await page.$eval('.tm-root', el => el.dataset.renderMs));
    check(rects >= 2000, '부하 장면 사각형 ' + rects + '개');
    console.log('  전체 다시 그리기 ' + rms + 'ms');
    await shot(page, 'stress');
    const sb = await stageBox(page);
    await page.evaluate(() => {
      window.__fr = []; window.__lt = 0; window.__hc = 0; let last = '';
      const r = document.querySelector('.tm-root');
      new MutationObserver(() => { if (r.dataset.hover !== last) { last = r.dataset.hover; } window.__hc++; }).observe(r, { attributes: true, attributeFilter: ['data-hover'] });
      try { new PerformanceObserver(l => { window.__lt += l.getEntries().length; }).observe({ type: 'longtask', buffered: false }); } catch (e) { }
      window.__run = true;
      const loop = t => { window.__fr.push(t); if (window.__run) requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    });
    const N = 240;
    for (let i = 0; i < N; i++) {
      const t = i / N;
      await page.mouse.move(sb.x + 6 + (sb.w - 12) * t, sb.y + sb.h * (0.15 + 0.7 * Math.abs(Math.sin(t * 9))));
    }
    await page.evaluate(() => { window.__run = false; });
    const perf = await page.evaluate(() => {
      const f = window.__fr, d = [];
      for (let i = 1; i < f.length; i++) d.push(f[i] - f[i - 1]);
      d.sort((a, b) => a - b);
      return { frames: f.length, p50: d[Math.floor(d.length * 0.5)], p95: d[Math.floor(d.length * 0.95)], max: d[d.length - 1], long: window.__lt, hoverChanges: window.__hc };
    });
    console.log('  호버 프레임 간격 p50 ' + perf.p50.toFixed(1) + 'ms · p95 ' + perf.p95.toFixed(1) + 'ms · 최대 ' + perf.max.toFixed(1) + 'ms · long task ' + perf.long + ' · 호버 변경 ' + perf.hoverChanges);
    check(perf.hoverChanges > 30, '부하 장면에서 호버가 따라옴');
    check(perf.long === 0, '호버 중 50ms 넘는 작업 없음');
    // 애니메이션 중 실제 프레임 간격(rAF). headless 는 소프트웨어 래스터라 실기(GPU)보다 느리다.
    async function animFrames(fn, ms) {
      await page.evaluate(() => { window.__af = []; window.__arun = true; const l = t => { window.__af.push(t); if (window.__arun) requestAnimationFrame(l); }; requestAnimationFrame(l); });
      await fn();
      await page.waitForTimeout(ms);
      return page.evaluate(() => { window.__arun = false; const f = window.__af, d = []; for (let i = 1; i < f.length; i++) d.push(f[i] - f[i - 1]); d.sort((a, b) => a - b); return { n: d.length, p50: d[Math.floor(d.length / 2)], p95: d[Math.floor(d.length * 0.95)], max: d[d.length - 1] }; });
    }
    const hl = await animFrames(() => page.evaluate(() => window.demo.tm.setHighlight(2)), 400);
    console.log('  하이라이트 전환 프레임 간격 p50 ' + hl.p50.toFixed(1) + ' · p95 ' + hl.p95.toFixed(1) + ' · 최대 ' + hl.max.toFixed(1) + 'ms');
    check(hl.p95 < 34, '하이라이트 전환 p95 < 34ms');
    await shot(page, 'stress-highlight');
    await page.evaluate(() => window.demo.tm.setHighlight(null));
    await page.waitForTimeout(300);
    const zm = await animFrames(() => page.mouse.click(sb.x + 30, sb.y + 8), 700);
    await waitReady(page);
    console.log('  확대 프레임 간격 p50 ' + zm.p50.toFixed(1) + ' · p95 ' + zm.p95.toFixed(1) + ' · 최대 ' + zm.max.toFixed(1) + 'ms');
    check(zm.p95 < 50, '부하 장면 확대 p95 < 50ms (소프트웨어 래스터 기준)');
    await page.close();
  } finally {
    await browser.close();
    srv.close();
  }
  if (errors.length) { console.log('콘솔 오류:'); errors.forEach(e => console.log('  ' + e)); failures.push('콘솔 오류 ' + errors.length + '건'); }
  if (failures.length) { console.log('\n실패 ' + failures.length + '건'); process.exit(1); }
  console.log('\n전부 통과 — 스크린샷: ' + OUT);
}

run().catch(e => { console.error(e); process.exit(1); });
