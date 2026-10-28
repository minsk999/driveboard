/* DriveBoard — 트리맵 (캔버스, 의존성 0).
 * window.Treemap.create(containerEl, {load, colorOf, catName, formatSize, onOpenItem, onContext, onPath})
 *   catName(catId) -> 툴팁 구성 줄에 붙일 카테고리 이름(없으면 색 점만)
 *   -> {open(dirId), up(), resize(), destroy(), setHighlight(catIdOrNull)}
 * node = GET /api/tree 의 node (SPEC §7): {id,name,path,size,cat,cat_bytes,kind,other?,children}, 깊이 2.
 * 안전: 'loose'(작은 파일 묶음, id·path 가 부모 폴더와 같다)·'other'·화면 묶음은 열기/우클릭 콜백을 절대 부르지 않는다.
 */
(function (global) {
  'use strict';
  if (global.Treemap && typeof global.Treemap.create === 'function') return;

  var NCAT = 11, OTHER_CAT = 10;
  var ZOOM_MS = 380, FADE_MS = 170, FIRST_MS = 280, HL_MS = 200, SHIMMER_DELAY = 140;
  var HDR = 20;          // 그룹 머리 띠 높이(css px)
  var GAP_TOP = 2;       // 최상위 사각형 사이 간격
  var GAP_IN = 1;        // 그룹 안 사각형 사이 간격
  var PAD = 2;           // 그룹 패널 안쪽 여백
  var MIN_AREA = 30;     // 이보다 작은 꼬리 항목은 하나의 조용한 블록으로 합친다(css px²)
  var GRID = 32;         // 히트 테스트 격자 칸
  var DIM = 0.82;        // 하이라이트 밖 항목을 배경색으로 덮는 정도
  var LITE_N = 1200;     // 이보다 칸이 많으면 확대·축소 중에는 음영·둥근 모서리를 생략한다(움직이는 동안은 안 보인다)
  var FONT = '"Pretendard Variable", Pretendard, -apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", sans-serif';
  var EASE = bezier(0.2, 0.8, 0.2, 1);

  var CSS = [
    '.tm-root{position:relative;width:100%;height:100%;overflow:hidden;contain:layout paint;',
    'user-select:none;-webkit-user-select:none;outline:none;',
    '--tm-bg:var(--bg,#0B0C10);--tm-panel:#16181F;--tm-text:rgba(255,255,255,.90);--tm-dim:rgba(255,255,255,.46);',
    '--tm-hover:rgba(255,255,255,.96);--tm-font:' + FONT + ';',
    'background:var(--tm-bg);font-family:var(--tm-font)}',
    '.tm-root>canvas{position:absolute;left:0;top:0;width:100%;height:100%;display:block}',
    '.tm-shim{position:absolute;inset:0;pointer-events:none;opacity:0;transition:opacity .28s ease;',
    'background:linear-gradient(105deg,transparent 38%,rgba(255,255,255,.055) 50%,transparent 62%) 0 0/250% 100% no-repeat;',
    'animation:tm-shim 1.6s cubic-bezier(.4,0,.6,1) infinite}',
    '.tm-root[data-loading] .tm-shim{opacity:1}',
    '@keyframes tm-shim{from{background-position:130% 0}to{background-position:-30% 0}}',
    '.tm-msg{position:absolute;inset:0;display:none;flex-direction:column;align-items:center;justify-content:center;gap:14px;',
    'padding:24px;text-align:center;pointer-events:none;color:var(--tm-dim);font-size:13px;line-height:1.5;letter-spacing:-.005em}',
    '.tm-msg.show{display:flex}',
    '.tm-msg .tm-ico{width:34px;height:34px;opacity:.55}',
    '.tm-msg button{pointer-events:auto;font:inherit;font-size:12px;font-weight:600;color:var(--tm-text);cursor:pointer;',
    'background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.10);border-radius:999px;padding:6px 14px;',
    'transition:background .15s ease}',
    '.tm-msg button:hover{background:rgba(255,255,255,.11)}',
    '.tm-toast{position:absolute;left:50%;bottom:14px;max-width:80%;transform:translate(-50%,6px);opacity:0;pointer-events:none;',
    'background:rgba(20,22,28,.94);color:var(--tm-text);border:1px solid rgba(255,255,255,.08);border-radius:10px;',
    'padding:8px 14px;font-size:12px;transition:opacity .2s ease,transform .2s cubic-bezier(.2,.8,.2,1);',
    'box-shadow:0 10px 30px rgba(0,0,0,.45)}',
    '.tm-toast.show{opacity:1;transform:translate(-50%,0)}',
    '.tm-tip{position:fixed;left:0;top:0;z-index:2147483000;pointer-events:none;opacity:0;transition:opacity .12s ease;',
    '--tm-tip-bg:rgba(19,21,27,.94);min-width:176px;max-width:320px;box-sizing:border-box;padding:10px 12px 11px;',
    'background:var(--tm-tip-bg);color:rgba(255,255,255,.92);border:1px solid rgba(255,255,255,.08);border-radius:11px;',
    'box-shadow:0 14px 34px rgba(0,0,0,.5),0 2px 6px rgba(0,0,0,.3);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);',
    'font-family:' + FONT + ';font-size:12px;line-height:1.35;font-variant-numeric:tabular-nums;letter-spacing:-.005em}',
    '.tm-tip.show{opacity:1}',
    '.tm-tip .tn{font-weight:650;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.tm-tip .tp{margin-top:2px;color:rgba(255,255,255,.42);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.tm-tip .tr{display:flex;align-items:baseline;gap:8px;margin-top:8px}',
    '.tm-tip .ts{font-weight:700;font-size:15px;letter-spacing:-.01em}',
    '.tm-tip .tq{color:rgba(255,255,255,.5);font-size:11px;margin-left:auto;white-space:nowrap}',
    '.tm-tip .tb{display:flex;gap:1px;height:5px;margin-top:8px;border-radius:3px;overflow:hidden;background:rgba(255,255,255,.06)}',
    '.tm-tip .tb i{display:block;height:100%;min-width:1px}',
    '.tm-tip .td{display:flex;flex-wrap:wrap;gap:3px 10px;margin-top:6px;color:rgba(255,255,255,.62);font-size:11px}',
    '.tm-tip .td span{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}',
    '.tm-tip .td b,.tm-tip .tm b{display:inline-block;width:7px;height:7px;border-radius:2px;flex:none}',
    '.tm-tip .tm{display:flex;align-items:center;gap:6px;margin-top:7px;color:rgba(255,255,255,.55);font-size:11px}',
    '.tm-tip .th{margin-top:7px;padding-top:7px;border-top:1px solid rgba(255,255,255,.07);color:rgba(255,255,255,.4);font-size:10.5px}',
    '.tm-tip [hidden]{display:none!important}',
    '@media (prefers-reduced-motion:reduce){.tm-shim{animation:none;background:rgba(255,255,255,.025)}',
    '.tm-tip,.tm-toast,.tm-shim{transition:none}}'
  ].join('');

  // ---------------------------------------------------------------- 유틸

  function bezier(p1x, p1y, p2x, p2y) {
    var cx = 3 * p1x, bx = 3 * (p2x - p1x) - cx, ax = 1 - cx - bx;
    var cy = 3 * p1y, by = 3 * (p2y - p1y) - cy, ay = 1 - cy - by;
    function sx(t) { return ((ax * t + bx) * t + cx) * t; }
    function sy(t) { return ((ay * t + by) * t + cy) * t; }
    function dx(t) { return (3 * ax * t + 2 * bx) * t + cx; }
    return function (x) {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      var t = x, i;
      for (i = 0; i < 8; i++) {
        var e = sx(t) - x;
        if (Math.abs(e) < 1e-6) return sy(t);
        var d = dx(t);
        if (Math.abs(d) < 1e-6) break;
        t -= e / d;
      }
      var lo = 0, hi = 1;
      t = x;
      for (i = 0; i < 40; i++) {
        var v = sx(t);
        if (Math.abs(v - x) < 1e-6) break;
        if (x > v) lo = t; else hi = t;
        t = (lo + hi) / 2;
      }
      return sy(t);
    };
  }

  function num(v) { v = +v; return isFinite(v) && v > 0 ? v : 0; }
  function key(id) { return id === null || id === undefined ? '' : String(id); }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function lerpR(a, b, t) { return { x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t), w: lerp(a.w, b.w, t), h: lerp(a.h, b.h, t) }; }
  function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
  function rgba(c, a) {
    return 'rgba(' + Math.round(c[0]) + ',' + Math.round(c[1]) + ',' + Math.round(c[2]) + ',' + (a === undefined ? 1 : +a.toFixed(3)) + ')';
  }
  function lum(c) {
    function ch(v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2]);
  }
  function isLoose(n) { return n.kind === 'loose'; }
  function isDir(n) {
    if (!n || n.other || isLoose(n)) return false;
    return n.kind === 'dir' || (!n.kind && Array.isArray(n.children));
  }
  function isSeq(n) { return n.kind === 'seq' || n.seq === true || num(n.count) > 1; }
  function drillable(n) { return isDir(n) && n.id !== null && n.id !== undefined; }
  function catOf(n) {
    var c = n && n.cat;
    return (typeof c === 'number' && c >= 0 && c < NCAT && (c | 0) === c) ? c : OTHER_CAT;
  }
  function catVec(n) {
    var v = new Array(NCAT), i, cb = n && n.cat_bytes;
    for (i = 0; i < NCAT; i++) v[i] = 0;
    if (Array.isArray(cb) && cb.length) {
      for (i = 0; i < NCAT && i < cb.length; i++) v[i] = num(cb[i]);
    } else if (n) {
      v[catOf(n)] = num(n.size);
    }
    return v;
  }
  function domCat(v, fallback) {
    var bi = -1, bv = 0;
    for (var i = 0; i < NCAT; i++) if (v[i] > bv) { bv = v[i]; bi = i; }
    return bi >= 0 ? bi : fallback;
  }
  function kidsOf(n) {
    var c = n && Array.isArray(n.children) ? n.children : [], out = [];
    for (var i = 0; i < c.length; i++) if (c[i] && typeof c[i] === 'object' && num(c[i].size) > 0) out.push(c[i]);
    return out;
  }
  function dispName(n) {
    if (!n) return '';
    if (n.name) return String(n.name);
    if (n.path) return String(n.path);
    return '루트';
  }
  function midTrunc(s, max) {
    s = String(s || '');
    var a = Array.from(s);
    if (a.length <= max) return s;
    var head = Math.ceil((max - 1) * 0.36), tail = max - 1 - head;
    return a.slice(0, head).join('') + '…' + a.slice(a.length - tail).join('');
  }
  function sig(n) {
    if (!n) return '';
    var s = [key(n.id), num(n.size), n.name || ''], k = n.children || [];
    for (var i = 0; i < k.length; i++) {
      var c = k[i] || {};
      s.push(key(c.id) + ':' + (c.kind || '') + ':' + num(c.size) + ':' + (c.name || '') + ':' + ((c.children && c.children.length) || 0));
    }
    return s.join('|');
  }

  // ---------------------------------------------------------------- squarified (Bruls·Huizing·van Wijk)

  function worst(side, s, rmax, rmin) {
    var s2 = s * s, w2 = side * side;
    return Math.max(w2 * rmax / s2, s2 / (w2 * rmin));
  }

  // items: [{n, v}] 넓이 내림차순, 합 ≈ w*h. out 에 {n, x, y, w, h} 를 넣는다.
  function squarify(items, x, y, w, h, out) {
    var i = 0, n = items.length;
    while (i < n) {
      if (!(w > 1e-9 && h > 1e-9)) {
        for (; i < n; i++) out.push({ n: items[i].n, x: x, y: y, w: 0, h: 0 });
        break;
      }
      var side = Math.min(w, h);
      var sum = items[i].v, rmax = sum, rmin = sum, wr = worst(side, sum, rmax, rmin);
      var j = i + 1;
      while (j < n) {
        var v = items[j].v, s2 = sum + v;
        var w2 = worst(side, s2, Math.max(rmax, v), Math.min(rmin, v));
        if (w2 > wr) break;
        sum = s2; rmax = Math.max(rmax, v); rmin = Math.min(rmin, v); wr = w2; j++;
      }
      var k, p;
      if (w >= h) {
        var sw = Math.min(w, sum / h), yy = y;
        for (k = i; k < j; k++) {
          p = k === j - 1 ? (y + h - yy) : items[k].v / sw;
          out.push({ n: items[k].n, x: x, y: yy, w: sw, h: p });
          yy += p;
        }
        x += sw; w -= sw;
      } else {
        var sh = Math.min(h, sum / w), xx = x;
        for (k = i; k < j; k++) {
          p = k === j - 1 ? (x + w - xx) : items[k].v / sh;
          out.push({ n: items[k].n, x: xx, y: y, w: p, h: sh });
          xx += p;
        }
        y += sh; h -= sh;
      }
      i = j;
    }
    return out;
  }

  function bySize(a, b) { return num(b.size) - num(a.size); }

  // 크기 순 배치 + 아주 작은 꼬리 항목은 한 블록(rest)으로 합친다. 서버의 'other' 묶음은 따로 남긴다.
  function layoutItems(nodes, x, y, w, h) {
    var out = [];
    if (!(w > 0.5 && h > 0.5) || !nodes.length) return out;
    var list = nodes.slice().sort(bySize), total = 0, i;
    for (i = 0; i < list.length; i++) total += num(list[i].size);
    if (total <= 0) return out;
    var k = (w * h) / total, tail = [], keep = [];
    i = list.length - 1;
    while (i >= 0 && num(list[i].size) * k < MIN_AREA) {
      (list[i].other ? keep : tail).push(list[i]);
      i--;
    }
    if (tail.length >= 2) {
      var s = 0;
      for (var t = 0; t < tail.length; t++) s += num(tail[t].size);
      tail.reverse();
      list = list.slice(0, i + 1).concat(keep, [{ __rest: true, size: s, members: tail }]);
      list.sort(bySize);
    }
    var items = new Array(list.length);
    for (i = 0; i < list.length; i++) items[i] = { n: list[i], v: num(list[i].size) * k };
    return squarify(items, x, y, w, h, out);
  }

  function injectStyle(doc) {
    if (doc.getElementById('tm-style')) return;
    var s = doc.createElement('style');
    s.id = 'tm-style';
    s.textContent = CSS;
    (doc.head || doc.documentElement).appendChild(s);
  }

  var ICON_EMPTY = '<svg class="tm-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/></svg>';
  var ICON_ERR = '<svg class="tm-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.8v5"/><path d="M12 16.2v.01"/></svg>';

  // ---------------------------------------------------------------- create

  function create(container, opts) {
    if (!container || typeof container.appendChild !== 'function') throw new TypeError('Treemap.create: 컨테이너 요소가 필요해요');
    opts = opts || {};
    if (typeof opts.load !== 'function') throw new TypeError('Treemap.create: load(dirId) 함수가 필요해요');
    var doc = container.ownerDocument || document;
    var win = doc.defaultView || global;
    injectStyle(doc);

    var root = doc.createElement('div');
    root.className = 'tm-root';
    root.setAttribute('role', 'img');
    root.setAttribute('aria-label', '트리맵');
    root.dataset.state = 'idle';
    var base = doc.createElement('canvas');
    var over = doc.createElement('canvas');
    var shim = doc.createElement('div'); shim.className = 'tm-shim';
    var msg = doc.createElement('div'); msg.className = 'tm-msg';
    var toast = doc.createElement('div'); toast.className = 'tm-toast'; toast.setAttribute('role', 'status');
    var probe = doc.createElement('i');
    probe.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden;visibility:hidden;pointer-events:none';
    root.appendChild(base); root.appendChild(over); root.appendChild(shim);
    root.appendChild(msg); root.appendChild(toast); root.appendChild(probe);
    container.appendChild(root);

    var bctx = base.getContext('2d', { alpha: false });
    var octx = over.getContext('2d');
    var snapA = doc.createElement('canvas'), snapB = doc.createElement('canvas');
    snapA.getContext('2d', { alpha: false });
    var sctx = snapB.getContext('2d', { alpha: false });
    var pix = doc.createElement('canvas'); pix.width = pix.height = 1;
    var pctx = pix.getContext('2d', { willReadFrequently: true });

    // 툴팁(본문에 붙여 컨테이너 overflow 에 잘리지 않게)
    var tip = doc.createElement('div'); tip.className = 'tm-tip'; tip.setAttribute('aria-hidden', 'true');
    tip.innerHTML = '<div class="tn"></div><div class="tp"></div><div class="tr"><span class="ts"></span><span class="tq"></span></div>' +
      '<div class="tb"></div><div class="td"></div><div class="tm"></div><div class="th"></div>';
    (doc.body || doc.documentElement).appendChild(tip);
    var T = {
      n: tip.querySelector('.tn'), p: tip.querySelector('.tp'), s: tip.querySelector('.ts'), q: tip.querySelector('.tq'),
      b: tip.querySelector('.tb'), d: tip.querySelector('.td'), m: tip.querySelector('.tm'), h: tip.querySelector('.th')
    };

    var S = {
      W: 0, H: 0, dpr: 1, node: null, scene: null, stack: [], cache: new Map(),
      hl: null, busy: false, tok: 0, hlTok: 0, dead: false, theme: null, catRgb: [], catCss: [], catCssR: [],
      tnum: false, tnumTried: false, tnumFace: null,
      hover: null, mx: -1, my: -1, inside: false, moveQueued: false, tipW: 0, tipH: 0, tipShown: false,
      hatch: null, frame: null, gen: 0, toastTimer: 0, rafResize: 0, mq: null, mqf: null
    };
    var MEAS = new Map();

    // ------------------------------------------------ 색·테마

    var colorCache = new Map();
    function resolve(str, fallback) {
      var s = String(str === undefined || str === null ? '' : str).trim();
      if (colorCache.has(s)) return colorCache.get(s);
      var out = fallback || [128, 128, 128];
      if (s) {
        try {
          probe.style.color = '';
          probe.style.color = s;
          if (probe.style.color) {
            var comp = win.getComputedStyle(probe).color;
            pctx.clearRect(0, 0, 1, 1);
            pctx.fillStyle = '#000';
            pctx.fillStyle = comp;
            pctx.fillRect(0, 0, 1, 1);
            var d = pctx.getImageData(0, 0, 1, 1).data;
            if (d[3] > 0) out = [d[0], d[1], d[2]];
          }
        } catch (e) { /* 기본색 */ }
      }
      colorCache.set(s, out);
      return out;
    }
    function callColor(i) {
      try { return opts.colorOf ? String(opts.colorOf(i)) : ''; } catch (e) { return ''; }
    }
    function callName(i) {
      try { return opts.catName ? String(opts.catName(i) || '') : ''; } catch (e) { return ''; }
    }
    function readTheme() {
      var cs = win.getComputedStyle(root);
      function v(n, f) { var x = cs.getPropertyValue(n); return (x && x.trim()) || f; }
      colorCache.clear();
      S.theme = {
        bg: resolve(v('--tm-bg', '#0B0C10'), [11, 12, 16]),
        panel: resolve(v('--tm-panel', '#16181F'), [22, 24, 31]),
        text: v('--tm-text', 'rgba(255,255,255,.90)'),
        dim: v('--tm-dim', 'rgba(255,255,255,.46)'),
        hover: v('--tm-hover', 'rgba(255,255,255,.96)'),
        font: v('--tm-font', FONT)
      };
      var fb = [[255, 159, 10], [47, 210, 127], [10, 132, 255], [48, 213, 200], [110, 107, 255], [191, 90, 242],
        [255, 79, 139], [255, 214, 10], [192, 163, 122], [142, 142, 147], [92, 92, 99]];
      for (var i = 0; i < NCAT; i++) {
        var c = callColor(i);
        S.catCss[i] = c || rgba(fb[i]);
        S.catRgb[i] = resolve(c, fb[i]);
        S.catCssR[i] = rgba(S.catRgb[i]);
      }
      S.hatch = null;
    }
    function fmt(b) {
      try { if (opts.formatSize) return String(opts.formatSize(b)); } catch (e) { /* 아래 */ }
      b = num(b);
      var u = ['KB', 'MB', 'GB', 'TB', 'PB'], x = b / 1024, i = 0;
      while (x >= 1000 && i < u.length - 1) { x /= 1024; i++; }
      return (x >= 100 ? x.toFixed(0) : String(+x.toPrecision(3))) + u[i];
    }

    // ------------------------------------------------ 장면(레이아웃)

    function entryOf(n, c, depth, parent, gap) {
      var t;
      if (n.__rest) t = 'rest';
      else if (n.other) t = 'other';
      else if (isLoose(n)) t = 'loose';
      else if (isDir(n)) t = 'dir';
      else if (!n.kind || n.kind === 'file' || n.kind === 'seq') t = 'file';
      else t = 'other';   // 모르는 종류는 조작할 수 없는 묶음으로 취급
      var cb, i;
      if (t === 'rest') {
        cb = new Array(NCAT);
        for (i = 0; i < NCAT; i++) cb[i] = 0;
        for (var m = 0; m < n.members.length; m++) {
          var mv = catVec(n.members[m]);
          for (i = 0; i < NCAT; i++) cb[i] += mv[i];
        }
      } else cb = catVec(n);
      var sum = 0;
      for (i = 0; i < NCAT; i++) sum += cb[i];
      var cat = t === 'file' ? catOf(n) : domCat(cb, catOf(n));
      var e = {
        t: t, n: n, x: c.x, y: c.y, w: c.w, h: c.h, gap: gap, depth: depth, parent: parent,
        cat: cat, cb: cb, cbSum: sum, ss: fmt(n.size), strip: null,
        em: 1, ga: 0, gf: 0, hm: 1, tem: 1, tga: 0, tgf: 0, thm: 1, lab: null, hdr: false,
        X: 0, Y: 0, DW: 0, DH: 0, R: 0
      };
      e.col = leafColor(e);
      e.fill = rgba(e.col);
      e.fkey = (t === 'other' ? 'o' : '') + e.fill;
      e.solid = t === 'file' || t === 'dir';
      e.light = lum(e.col) < 0.36 || !e.solid;
      if (t === 'dir' && sum > 0) {
        // 여러 카테고리가 섞인 폴더 칸에는 아래쪽에 얇은 구성 띠를 둔다
        var segs = [];
        for (i = 0; i < NCAT; i++) if (cb[i] / sum >= 0.01) segs.push([i, cb[i] / sum]);
        segs.sort(function (a2, b2) { return b2[1] - a2[1]; });
        if (segs.length >= 2 && segs[0][1] < 0.97 && segs[1][1] >= 0.03) {
          var tot = 0;
          for (i = 0; i < segs.length; i++) tot += segs[i][1];
          for (i = 0; i < segs.length; i++) segs[i][1] /= tot;
          e.strip = segs;
        }
      }
      return e;
    }

    function buildScene(node, W, H) {
      var sc = { node: node, W: W, H: H, list: [], total: 0, grid: null, cols: 0, rows: 0 };
      var kids = kidsOf(node), sum = 0, i;
      for (i = 0; i < kids.length; i++) sum += num(kids[i].size);
      sc.total = Math.max(num(node && node.size), sum);
      if (W > 0 && H > 0 && sum > 0) {
        var cells = layoutItems(kids, 0, 0, W, H);
        for (i = 0; i < cells.length; i++) {
          var c = cells[i];
          if (!(c.w > 0 && c.h > 0)) continue;
          var e = entryOf(c.n, c, 1, null, GAP_TOP);
          sc.list.push(e);
          if (e.t !== 'dir') continue;
          var gk = kidsOf(c.n);
          if (!gk.length || c.w < 22 || c.h < 22) continue;
          var hdr = c.w >= 52 && c.h >= 42;
          var ins = GAP_TOP / 2 + PAD, top = hdr ? GAP_TOP / 2 + HDR : ins;
          var cw = c.w - ins * 2, ch = c.h - top - ins;
          if (cw < 6 || ch < 6) continue;
          e.t = 'group'; e.hdr = hdr; e.cx = c.x + ins; e.cy = c.y + top; e.cw = cw; e.ch = ch; e.kids = [];
          var g = layoutItems(gk, e.cx, e.cy, cw, ch);
          for (var j = 0; j < g.length; j++) {
            if (!(g[j].w > 0 && g[j].h > 0)) continue;
            var ge = entryOf(g[j].n, g[j], 2, e, GAP_IN);
            sc.list.push(ge); e.kids.push(ge);
          }
        }
      }
      buildGrid(sc);
      hlTargets(sc, S.hl);
      hlSnap(sc);
      return sc;
    }

    function buildGrid(sc) {
      var cols = Math.max(1, Math.ceil(sc.W / GRID)), rows = Math.max(1, Math.ceil(sc.H / GRID));
      var grid = new Array(cols * rows), i;
      for (i = 0; i < grid.length; i++) grid[i] = [];
      for (i = 0; i < sc.list.length; i++) {
        var e = sc.list[i];
        var c0 = clamp(Math.floor(e.x / GRID), 0, cols - 1), c1 = clamp(Math.floor((e.x + e.w - 1e-6) / GRID), 0, cols - 1);
        var r0 = clamp(Math.floor(e.y / GRID), 0, rows - 1), r1 = clamp(Math.floor((e.y + e.h - 1e-6) / GRID), 0, rows - 1);
        for (var r = r0; r <= r1; r++) for (var c = c0; c <= c1; c++) grid[r * cols + c].push(i);
      }
      sc.grid = grid; sc.cols = cols; sc.rows = rows;
    }

    function hitTest(sc, px, py) {
      if (!sc || !sc.grid || px < 0 || py < 0 || px >= sc.W || py >= sc.H) return null;
      var c = Math.floor(px / GRID), r = Math.floor(py / GRID);
      if (c >= sc.cols || r >= sc.rows) return null;
      var cell = sc.grid[r * sc.cols + c];
      for (var i = cell.length - 1; i >= 0; i--) {
        var e = sc.list[cell[i]];
        if (px >= e.x && px < e.x + e.w && py >= e.y && py < e.y + e.h) return e;
      }
      return null;
    }

    // ------------------------------------------------ 하이라이트 목표값

    function normCat(c) {
      if (c === null || c === undefined || c === '' || c === -1) return null;
      c = +c;
      return isFinite(c) && c >= 0 && c < NCAT ? (c | 0) : null;
    }
    function hlTargets(sc, hl) {
      for (var i = 0; i < sc.list.length; i++) {
        var e = sc.list[i], em = 1, ga = 0, gf = 0, hm = 1;
        if (hl !== null) {
          if (e.t === 'group') hm = e.cb[hl] > 0 ? 1 : 0.34;
          else if (e.t === 'file') em = e.cat === hl ? 1 : 0;
          else if (e.t === 'other' && !Array.isArray(e.n.cat_bytes)) em = 0;
          else {
            var f = e.cbSum > 0 ? e.cb[hl] / e.cbSum : 0;
            if (f >= 0.985) em = 1;
            else { em = 0; if (f > 0) { ga = 1; gf = f; } }
          }
        }
        e.tem = em; e.tga = ga; e.tgf = gf; e.thm = hm;
      }
    }
    function hlSnap(sc) {
      for (var i = 0; i < sc.list.length; i++) {
        var e = sc.list[i];
        e.em = e.tem; e.ga = e.tga; e.gf = e.tgf; e.hm = e.thm;
      }
    }

    // ------------------------------------------------ 글자

    function mw(g, font, s) {
      var k = font + '\u0001' + s, v = MEAS.get(k);
      if (v === undefined) {
        g.font = font;
        v = g.measureText(s).width;
        if (MEAS.size > 30000) MEAS.clear();
        MEAS.set(k, v);
      }
      return v;
    }
    function fit(g, font, s, max) {
      s = String(s || '');
      if (max <= 0 || !s) return '';
      if (mw(g, font, s) <= max) return s;
      var ell = '…';
      if (mw(g, font, ell) > max) return '';
      var cp = Array.from(s), lo = 0, hi = cp.length - 1;
      while (lo < hi) {
        var mid = (lo + hi + 1) >> 1;
        if (mw(g, font, cp.slice(0, mid).join('') + ell) <= max) lo = mid; else hi = mid - 1;
      }
      // 두세 글자만 남는 잘림("02…")은 정보가 아니라 잡음이다
      var head = cp.slice(0, lo).join('').replace(/[\s_.\-(\[]+$/, ''), hl = Array.from(head).length;
      if (!hl || (hl < 4 && hl < cp.length * 0.5)) return '';
      return head + ell;
    }

    // 캔버스는 font-variant-numeric 을 모른다. 같은 글꼴 파일을 tnum 을 켠 별칭으로 한 번 더 등록해 숫자를 표 숫자로 그린다.
    var TNUM = 'TM Tabular';
    function numFont(weight, px) { return weight + ' ' + px + 'px ' + (S.tnum ? '"' + TNUM + '", ' : '') + S.theme.font; }
    function firstFamily(list) { return String(list || '').split(',')[0].trim().replace(/^["']|["']$/g, ''); }
    function findFace(fam) {
      var want = fam.toLowerCase(), found = null;
      function walk(rules, href) {
        for (var i = 0; rules && i < rules.length && !found; i++) {
          var r = rules[i];
          if (r.type === 5 && r.style) {
            var f = firstFamily(r.style.getPropertyValue('font-family')).toLowerCase();
            if (f === want) found = { src: r.style.getPropertyValue('src'), weight: r.style.getPropertyValue('font-weight'), href: href };
          } else if (r.cssRules) walk(r.cssRules, href);
        }
      }
      var sheets = doc.styleSheets || [];
      for (var i = 0; i < sheets.length && !found; i++) {
        var rules = null;
        try { rules = sheets[i].cssRules; } catch (e) { rules = null; }
        walk(rules, sheets[i].href || doc.baseURI);
      }
      return found;
    }
    function setupTnum() {
      if (S.tnumTried || !win.FontFace || !doc.fonts || !doc.fonts.add) return;
      S.tnumTried = true;
      try {
        var fams = String(S.theme.font).split(','), face = null;
        for (var i = 0; i < fams.length && !face; i++) {
          var f = firstFamily(fams[i]);
          if (f && !/^(serif|sans-serif|monospace|system-ui|-apple-system|blinkmacsystemfont)$/i.test(f)) face = findFace(f);
        }
        if (!face || !face.src) return;
        var src = face.src.replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, function (m, q, u) {
          try { return 'url("' + new URL(u, face.href).href + '")'; } catch (e) { return m; }
        });
        var ff = new win.FontFace(TNUM, src, { weight: (face.weight || '100 900').trim() || '100 900', featureSettings: '"tnum"' });
        doc.fonts.add(ff);
        S.tnumFace = ff;
        ff.load().then(function () { if (S.dead) return; S.tnum = true; onFonts(); }, function () { /* 비례 숫자로 둔다 */ });
      } catch (e) { /* 비례 숫자로 둔다 */ }
    }

    // ------------------------------------------------ 그리기

    function hatch(g) {
      if (S.hatch) return S.hatch;
      var s = Math.max(4, Math.round(6 * S.dpr)), c = doc.createElement('canvas');
      c.width = c.height = s;
      var x = c.getContext('2d');
      x.strokeStyle = 'rgba(255,255,255,0.085)';
      x.lineWidth = Math.max(1, S.dpr * 0.9);
      x.beginPath();
      x.moveTo(-1, s + 1); x.lineTo(s + 1, -1);
      x.moveTo(-1, 1); x.lineTo(1, -1);
      x.moveTo(s - 1, s + 1); x.lineTo(s + 1, s - 1);
      x.stroke();
      S.hatch = g.createPattern(c, 'repeat');
      return S.hatch;
    }

    // 칸마다 그라디언트를 만들지 않고, 세로 음영 한 장을 늘려 덧칠한다(위는 아주 옅게 밝고 아래로 갈수록 가라앉는다)
    var SHADE = null;
    function shade() {
      if (SHADE) return SHADE;
      SHADE = doc.createElement('canvas');
      SHADE.width = 1; SHADE.height = 256;
      var x = SHADE.getContext('2d'), lg = x.createLinearGradient(0, 0, 0, 256);
      lg.addColorStop(0, 'rgba(255,255,255,0.075)');
      lg.addColorStop(0.18, 'rgba(255,255,255,0.02)');
      lg.addColorStop(0.5, 'rgba(0,0,0,0)');
      lg.addColorStop(1, 'rgba(0,0,0,0.30)');
      x.fillStyle = lg;
      x.fillRect(0, 0, 1, 256);
      // 캔버스→캔버스 drawImage 는 소프트웨어 래스터에서 매번 스냅숏을 뜬다. 가능하면 ImageBitmap 으로 바꿔 둔다.
      try {
        if (win.OffscreenCanvas) {
          var oc = new win.OffscreenCanvas(1, 256), ox = oc.getContext('2d');
          ox.drawImage(SHADE, 0, 0);
          SHADE = oc.transferToImageBitmap();
        }
      } catch (e) { /* 캔버스 그대로 쓴다 */ }
      return SHADE;
    }

    function rr(g, x, y, w, h, r) {
      g.beginPath();
      if (r > 0.5 && g.roundRect) g.roundRect(x, y, w, h, r);
      else g.rect(x, y, w, h);
    }
    function addR(g, e) {
      if (e.R > 0.5 && g.roundRect) g.roundRect(e.X, e.Y, e.DW, e.DH, e.R);
      else g.rect(e.X, e.Y, e.DW, e.DH);
    }

    function nameOf(e) {
      if (e.t === 'rest') return '작은 항목 ' + e.n.members.length.toLocaleString('ko-KR') + '개';
      if (e.t === 'loose') return num(e.n.files) > 0 ? '작은 파일 ' + num(e.n.files).toLocaleString('ko-KR') + '개' : '작은 파일들';
      return dispName(e.n);
    }
    function tipName(e) { return e.t === 'loose' ? (e.n.name || '이 폴더의 작은 파일들') : nameOf(e); }

    function leafColor(e) {
      var th = S.theme;
      if (e.t === 'other') return mix(th.panel, S.catRgb[OTHER_CAT], 0.42);
      if (e.t === 'rest') return mix(th.panel, S.catRgb[e.cat], 0.30);
      if (e.t === 'loose') return mix(th.panel, S.catRgb[e.cat], 0.48);
      return S.catRgb[e.cat];
    }

    // 잎 글자 배치: 이름 / 크기 / (섞인 폴더면) 구성 미니 막대
    function labelBox(e) {
      var dpr = S.dpr, wc = e.DW / dpr, hc = e.DH / dpr, nf, sf, pad;
      if (e.depth === 1) {
        if (wc >= 150 && hc >= 80) { nf = 13.5; sf = 12; pad = 9; }
        else if (wc >= 46 && hc >= 22) { nf = 12; sf = 11; pad = 7; }
        else return null;
      } else {
        if (wc >= 40 && hc >= 19) { nf = 11; sf = 10.5; pad = 5.5; }
        else return null;
      }
      var base1 = pad + nf * 0.86, base2 = base1 + sf * 1.36;
      var showSize = hc >= pad * 2 + nf * 1.1 + sf * 1.3;
      var bar = !!(e.strip && showSize && wc >= 56 && hc >= base2 + 8 + 3 + pad);
      return { nf: nf, sf: sf, pad: pad, b1: base1, b2: base2, size: showSize, bar: bar };
    }

    function leafLabel(g, e, alpha) {
      var B = labelBox(e);
      if (!B) return;
      var dpr = S.dpr, nf = B.nf, sf = B.sf, pad = B.pad;
      var fN = '600 ' + (nf * dpr) + 'px ' + S.theme.font, fS = numFont(500, sf * dpr);
      var maxW = e.DW - pad * 2 * dpr;
      var showSize = B.size;
      var k = e.DW + '|' + fN + '|' + fS;
      if (!e.lab || e.lab.k !== k) {
        e.lab = { k: k, name: fit(g, fN, nameOf(e), maxW), size: mw(g, fS, e.ss) <= maxW ? e.ss : '' };
      }
      var L = e.lab;
      if (!L.name) return;
      var light = e.em < 1 ? lum(mix(e.col, S.theme.bg, (1 - e.em) * DIM)) < 0.36 || !e.solid : e.light;
      var a = alpha * (0.3 + 0.7 * e.em);
      if (e.t === 'rest' || e.t === 'other') a *= 0.82;
      var xt = e.X + pad * dpr, yt = e.Y + B.b1 * dpr;
      g.globalAlpha = a;
      g.font = fN;
      g.fillStyle = light ? 'rgba(255,255,255,0.96)' : 'rgba(9,10,14,0.86)';
      g.fillText(L.name, xt, yt);
      if (showSize && L.size) {
        yt = e.Y + B.b2 * dpr;
        g.font = fS;
        g.fillStyle = light ? 'rgba(255,255,255,0.64)' : 'rgba(9,10,14,0.6)';
        g.fillText(L.size, xt, yt);
      }
      g.globalAlpha = 1;
    }

    function headerLabel(g, e, hh, alpha) {
      var dpr = S.dpr, th = S.theme;
      var fN = '600 ' + (11.5 * dpr) + 'px ' + th.font, fS = numFont(500, 11 * dpr);
      var pad = 7 * dpr, avail = e.DW - pad * 2;
      if (avail < 20 * dpr) return;
      var k = 'h' + e.DW + '|' + fN + '|' + fS;
      if (!e.lab || e.lab.k !== k) {
        var sw = mw(g, fS, e.ss), nm = '', sz = '';
        if (avail >= sw + 10 * dpr + 30 * dpr) { nm = fit(g, fN, nameOf(e), avail - sw - 10 * dpr); sz = e.ss; }
        if (!nm) { nm = fit(g, fN, nameOf(e), avail); sz = nm ? '' : (sw <= avail ? e.ss : ''); }
        e.lab = { k: k, name: nm, size: sz, sw: sw };
      }
      var L = e.lab, yb = e.Y + hh / 2 + 11.5 * dpr * 0.36;
      g.globalAlpha = alpha * e.hm;
      if (L.name) { g.font = fN; g.fillStyle = th.text; g.fillText(L.name, e.X + pad, yb); }
      if (L.size) { g.font = fS; g.fillStyle = th.dim; g.fillText(L.size, e.X + e.DW - pad - L.sw, yb); }
      g.globalAlpha = 1;
    }

    function drawStrip(g, e) {
      var B = labelBox(e);
      if (!B || !B.bar) return;
      var dpr = S.dpr, h = Math.max(2, Math.round(3 * dpr)), m = Math.max(1, Math.round(dpr));
      var x = Math.round(e.X + B.pad * dpr), y = Math.round(e.Y + (B.b2 + 7) * dpr);
      var w = Math.round(Math.min(e.DW - B.pad * 2 * dpr, 72 * dpr));
      if (w < 24 * dpr) return;
      g.fillStyle = e.light ? 'rgba(0,0,0,0.32)' : 'rgba(0,0,0,0.22)';
      rr(g, x - m, y - m, w + 2 * m, h + 2 * m, (h + 2 * m) / 2);
      g.fill();
      var cx = x, segs = e.strip, end = x + w;
      for (var i = 0; i < segs.length && cx < end; i++) {
        var sw = i === segs.length - 1 ? end - cx : Math.max(dpr, Math.round(w * segs[i][1]));
        sw = Math.min(sw, end - cx);
        g.fillStyle = S.catCssR[segs[i][0]];
        g.fillRect(cx, y, sw, h);
        cx += sw;
      }
    }

    // cam = 화면에 보이는 장면 좌표 사각형. o = {labels:0..1, focus:rect|null, fade:0..1}
    // 같은 칠끼리 한 경로로 모아 한 번에 채운다(수천 칸에서도 한 프레임 안에).
    var VIS = [], BUCK = new Map();
    function paint(g, sc, cam, o) {
      var dpr = S.dpr, Wd = base.width, Hd = base.height, th = S.theme, i, e, arr;
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.globalAlpha = 1;
      g.fillStyle = rgba(th.bg);
      g.fillRect(0, 0, Wd, Hd);
      if (!sc) return 0;
      var kx = Wd / cam.w, ky = Hd / cam.h, L = sc.list, lite = !!o.lite;
      VIS.length = 0;
      for (i = 0; i < L.length; i++) {
        e = L[i];
        var half = e.gap * dpr / 2;
        var x0 = Math.round((e.x - cam.x) * kx + half), x1 = Math.round((e.x + e.w - cam.x) * kx - half);
        var y0 = Math.round((e.y - cam.y) * ky + half), y1 = Math.round((e.y + e.h - cam.y) * ky - half);
        if (x1 <= 0 || y1 <= 0 || x0 >= Wd || y0 >= Hd || x1 - x0 < 1 || y1 - y0 < 1) continue;
        e.X = x0; e.Y = y0; e.DW = x1 - x0; e.DH = y1 - y0;
        if (e.t === 'group') e.R = Math.min(5 * dpr, e.DW / 3, e.DH / 3);
        else {
          var mn = Math.min(e.DW, e.DH);
          e.R = mn >= (lite ? 16 : 10) * dpr ? Math.min(2.5 * dpr, mn / 4) : 0;
        }
        VIS.push(e);
      }
      var n = VIS.length, any = false, hh = (HDR - GAP_TOP / 2) * ky, line = Math.max(1, Math.round(dpr * 0.75));
      // 1) 그룹 패널 + 머리 띠 아래 가는 선
      g.beginPath();
      for (i = 0; i < n; i++) if (VIS[i].t === 'group') { addR(g, VIS[i]); any = true; }
      if (any) {
        g.fillStyle = rgba(th.panel);
        g.fill();
        g.beginPath();
        for (i = 0; i < n; i++) { e = VIS[i]; if (e.t === 'group' && e.hdr && e.DH > hh + 2 * dpr) g.rect(e.X, e.Y + hh, e.DW, line); }
        g.fillStyle = 'rgba(255,255,255,0.03)';
        g.fill();
      }
      // 2) 잎: 칠별 한 경로
      BUCK.clear();
      for (i = 0; i < n; i++) {
        e = VIS[i];
        if (e.t === 'group') continue;
        arr = BUCK.get(e.fkey);
        if (!arr) BUCK.set(e.fkey, arr = []);
        arr.push(e);
      }
      BUCK.forEach(function (list) {
        g.beginPath();
        for (var j = 0; j < list.length; j++) addR(g, list[j]);
        g.fillStyle = list[0].fill;
        g.fill();
        if (list[0].t === 'other') { g.fillStyle = hatch(g); g.fill(); }
      });
      // 3) 음영 + 윗변 하이라이트
      var sh = shade();
      g.imageSmoothingEnabled = false;   // 256단 음영이라 최근접 표본으로도 띠가 보이지 않고, 소프트웨어 래스터에서 훨씬 싸다
      g.beginPath();
      for (i = 0; i < n; i++) {
        e = VIS[i];
        if (!e.solid || e.t === 'group') continue;
        if (!lite && e.DH >= 10 * dpr && e.DW >= 4 * dpr) g.drawImage(sh, 0, 0, 1, 256, e.X, e.Y, e.DW, e.DH);
        if (e.DW >= 12 * dpr && e.DH >= 12 * dpr) g.rect(e.X + e.R, e.Y, e.DW - 2 * e.R, line);
      }
      g.imageSmoothingEnabled = true;
      g.fillStyle = 'rgba(255,255,255,0.12)';
      g.fill();
      // 4) 섞인 폴더의 구성 미니 막대(글자와 함께 나타나고 사라진다)
      if (o.labels > 0.01) {
        g.globalAlpha = o.labels;
        for (i = 0; i < n; i++) if (VIS[i].strip && VIS[i].t === 'dir') drawStrip(g, VIS[i]);
        g.globalAlpha = 1;
      }
      // 5) 하이라이트 밖은 배경색으로 덮어 가라앉힌다(투명도별로 묶어서)
      BUCK.clear();
      for (i = 0; i < n; i++) {
        e = VIS[i];
        if (e.t === 'group' || e.em >= 0.999) continue;
        var ak = Math.round((1 - e.em) * DIM * 100);
        if (ak <= 0) continue;
        arr = BUCK.get(ak);
        if (!arr) BUCK.set(ak, arr = []);
        arr.push(e);
      }
      BUCK.forEach(function (list, ak) {
        g.beginPath();
        for (var j = 0; j < list.length; j++) addR(g, list[j]);
        g.fillStyle = rgba(th.bg, ak / 100);
        g.fill();
      });
      // 6) 섞인 칸은 하이라이트 카테고리 몫만큼 아래에서 채운다
      if (S.hl !== null) {
        BUCK.clear();
        for (i = 0; i < n; i++) {
          e = VIS[i];
          if (e.t === 'group' || !(e.ga > 0.01 && e.gf > 0)) continue;
          var gk = Math.round(e.ga * 40);
          arr = BUCK.get(gk);
          if (!arr) BUCK.set(gk, arr = []);
          arr.push(e);
        }
        var hc = S.catRgb[S.hl];
        BUCK.forEach(function (list, gk) {
          g.globalAlpha = gk / 40;
          g.beginPath();
          var tops = [];
          for (var j = 0; j < list.length; j++) {
            var x = list[j], gh = Math.max(1, Math.round(x.DH * x.gf)), gy = x.Y + x.DH - gh;
            if (x.R > 0.5 && g.roundRect) g.roundRect(x.X, gy, x.DW, gh, [0, 0, x.R, x.R]);
            else g.rect(x.X, gy, x.DW, gh);
            if (gh >= 3 * dpr && gh < x.DH - 1) tops.push(x.X, gy, x.DW);
          }
          g.fillStyle = rgba(hc);
          g.fill();
          g.beginPath();
          for (var t = 0; t < tops.length; t += 3) g.rect(tops[t], tops[t + 1], tops[t + 2], line);
          g.fillStyle = 'rgba(255,255,255,0.2)';
          g.fill();
          g.globalAlpha = 1;
        });
      }
      // 7) 글자
      if (o.labels > 0.01) {
        g.textAlign = 'left';
        g.textBaseline = 'alphabetic';
        for (i = 0; i < n; i++) {
          e = VIS[i];
          if (e.t === 'group') { if (e.hdr && hh >= 13 * dpr) headerLabel(g, e, hh, o.labels); }
          else leafLabel(g, e, o.labels);
        }
      }
      // 8) 확대 중: 초점 밖을 서서히 가라앉힌다
      if (o.focus && o.fade > 0.001) {
        var f = o.focus, fx = (f.x - cam.x) * kx, fy = (f.y - cam.y) * ky;
        g.fillStyle = rgba(th.bg, o.fade * 0.6);
        g.beginPath();
        g.rect(0, 0, Wd, Hd);
        g.rect(fx, fy, f.w * kx, f.h * ky);
        g.fill('evenodd');
      }
      return n;
    }

    function paintSkeleton(g) {
      var th = S.theme, dpr = S.dpr, W = S.W, H = S.H;
      var ws = [30, 18, 12, 9, 7, 6, 5, 4, 3, 3, 2, 1];
      var items = [], tot = 0, i;
      for (i = 0; i < ws.length; i++) tot += ws[i];
      for (i = 0; i < ws.length; i++) items.push({ n: i, v: ws[i] / tot * W * H });
      var cells = squarify(items, 0, 0, W, H, []);
      for (i = 0; i < cells.length; i++) {
        var c = cells[i], half = GAP_TOP * dpr / 2;
        var x0 = Math.round(c.x * dpr + half), x1 = Math.round((c.x + c.w) * dpr - half);
        var y0 = Math.round(c.y * dpr + half), y1 = Math.round((c.y + c.h) * dpr - half);
        if (x1 - x0 < 2 || y1 - y0 < 2) continue;
        rr(g, x0, y0, x1 - x0, y1 - y0, Math.min(5 * dpr, (x1 - x0) / 3, (y1 - y0) / 3));
        g.fillStyle = rgba(mix(th.panel, th.bg, 0.25));
        g.fill();
        if (y1 - y0 > 40 * dpr && x1 - x0 > 90 * dpr) {
          g.fillStyle = 'rgba(255,255,255,0.045)';
          rr(g, x0 + 8 * dpr, y0 + 7 * dpr, Math.min(110 * dpr, (x1 - x0) * 0.45), 6 * dpr, 3 * dpr);
          g.fill();
        }
      }
    }

    function fullCam(sc) { return { x: 0, y: 0, w: sc.W || 1, h: sc.H || 1 }; }

    function renderStatic(g) {
      if (S.dead || !(S.W > 0 && S.H > 0)) return;
      var t0 = (win.performance || Date).now();
      g = g || bctx;
      var n = 0;
      if (S.scene) n = paint(g, S.scene, fullCam(S.scene), { labels: 1 });
      else {
        paint(g, null, null, {});
        if (root.dataset.state === 'loading') paintSkeleton(g);
      }
      root.dataset.renderMs = ((win.performance || Date).now() - t0).toFixed(1);
      root.dataset.rects = String(n);
      if (g === bctx) drawHover();
      return n;
    }

    // ------------------------------------------------ 호버

    function devRect(e, gap) {
      var dpr = S.dpr, half = (gap === undefined ? e.gap : gap) * dpr / 2;
      var x0 = Math.round(e.x * dpr + half), x1 = Math.round((e.x + e.w) * dpr - half);
      var y0 = Math.round(e.y * dpr + half), y1 = Math.round((e.y + e.h) * dpr - half);
      return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
    }

    function drawHover() {
      octx.setTransform(1, 0, 0, 1, 0, 0);
      octx.clearRect(0, 0, over.width, over.height);
      var e = S.hover, dpr = S.dpr;
      if (!e || S.busy || !S.scene) return;
      if (e.parent) {
        var p = devRect(e.parent), pr = Math.min(5 * dpr, p.w / 3, p.h / 3);
        octx.lineWidth = dpr;
        octx.strokeStyle = 'rgba(255,255,255,0.20)';
        rr(octx, p.x + dpr / 2, p.y + dpr / 2, p.w - dpr, p.h - dpr, pr);
        octx.stroke();
      }
      var d = devRect(e);
      if (d.w < 1 || d.h < 1) return;
      if (e.t === 'group') {
        var gr = Math.min(5 * dpr, d.w / 3, d.h / 3);
        if (e.hdr) {
          octx.save();
          rr(octx, d.x, d.y, d.w, d.h, gr);
          octx.clip();
          octx.fillStyle = 'rgba(255,255,255,0.045)';
          octx.fillRect(d.x, d.y, d.w, Math.round((HDR - GAP_TOP / 2) * dpr));
          octx.restore();
        }
        octx.lineWidth = 1.25 * dpr;
        octx.strokeStyle = 'rgba(255,255,255,0.62)';
        rr(octx, d.x + 0.625 * dpr, d.y + 0.625 * dpr, d.w - 1.25 * dpr, d.h - 1.25 * dpr, gr);
        octx.stroke();
        return;
      }
      var r = (d.w >= 8 * dpr && d.h >= 8 * dpr) ? Math.min(2.5 * dpr, d.w / 3, d.h / 3) : 0;
      rr(octx, d.x, d.y, d.w, d.h, r);
      octx.fillStyle = e.t === 'other' || e.t === 'rest' || e.t === 'loose' ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.12)';
      octx.fill();
      var lw = Math.min(1.5 * dpr, d.w / 2, d.h / 2);
      octx.lineWidth = lw;
      octx.strokeStyle = S.theme.hover;
      rr(octx, d.x + lw / 2, d.y + lw / 2, d.w - lw, d.h - lw, Math.max(0, r - lw / 2));
      octx.stroke();
    }

    function hoverKind(e) {
      if (!e) return '';
      if (e.t === 'file') return isSeq(e.n) ? 'seq' : 'file';
      return e.t;
    }
    function clickable(e) {
      if (!e) return false;
      if (e.t === 'group' || e.t === 'dir') return drillable(e.n);
      if (e.t === 'file') return true;
      if (e.t === 'rest') return !!(e.parent && drillable(e.parent.n));
      return false;
    }

    function setHover(e) {
      if (e === S.hover) return;
      S.hover = e;
      root.dataset.hover = hoverKind(e);
      root.style.cursor = clickable(e) ? 'pointer' : '';
      drawHover();
      if (e) { fillTip(e); showTip(true); } else showTip(false);
    }

    function showTip(on) {
      if (S.tipShown === on) return;
      S.tipShown = on;
      tip.classList.toggle('show', on);
    }

    function pct(sz) {
      var tot = S.scene ? S.scene.total : 0;
      if (!(tot > 0)) return '';
      var p = num(sz) / tot * 100;
      if (p >= 99.95) return '현재 폴더의 100%';
      if (p < 0.1) return '현재 폴더의 0.1% 미만';
      return '현재 폴더의 ' + (p >= 10 ? p.toFixed(1) : p.toFixed(1)) + '%';
    }

    function fillTip(e) {
      var n = e.n, i;
      T.n.textContent = tipName(e);
      var path = (e.t === 'rest' || e.t === 'other') ? (e.parent ? e.parent.n.path : (S.node && S.node.path)) : n.path;
      T.p.textContent = path ? midTrunc(path, 58) : '';
      T.p.hidden = !path;
      T.s.textContent = e.ss;
      T.q.textContent = pct(n.size);
      // 구성 막대 (폴더·묶음)
      while (T.b.firstChild) T.b.removeChild(T.b.firstChild);
      while (T.d.firstChild) T.d.removeChild(T.d.firstChild);
      var multi = e.t !== 'file' && e.cbSum > 0 && (e.t !== 'other' || Array.isArray(n.cat_bytes));
      T.b.hidden = !multi; T.d.hidden = !multi;
      if (multi) {
        var order = [];
        for (i = 0; i < NCAT; i++) if (e.cb[i] > 0) order.push(i);
        order.sort(function (a, b) { return e.cb[b] - e.cb[a]; });
        for (i = 0; i < order.length; i++) {
          var seg = doc.createElement('i');
          seg.style.width = (e.cb[order[i]] / e.cbSum * 100).toFixed(3) + '%';
          seg.style.background = S.catCss[order[i]];
          T.b.appendChild(seg);
        }
        for (i = 0; i < order.length && i < 3; i++) {
          var p = e.cb[order[i]] / e.cbSum * 100;
          var sp = doc.createElement('span'), dot = doc.createElement('b');
          dot.style.background = S.catCss[order[i]];
          sp.appendChild(dot);
          // 다른 카테고리가 조금이라도 있으면 맨 위가 100% 로 반올림되지 않게(「100% · 1% 미만」 은 합이 넘쳐 보인다)
          var pt = p >= 99.5 ? (order.length > 1 ? '99%' : '100%') : p < 1 ? '1% 미만' : Math.round(p) + '%';
          var cn = callName(order[i]);
          sp.appendChild(doc.createTextNode(cn ? cn + ' ' + pt : pt));
          T.d.appendChild(sp);
        }
      }
      // 종류
      while (T.m.firstChild) T.m.removeChild(T.m.firstChild);
      var meta = '', hint = '';
      if (e.t === 'group' || e.t === 'dir') {
        meta = '폴더';
        if (num(n.files) > 0) meta += ' · 파일 ' + num(n.files).toLocaleString('ko-KR') + '개';
        hint = drillable(n) ? '클릭하면 안으로 들어가요' : '';
      } else if (e.t === 'file') {
        var dot2 = doc.createElement('b');
        dot2.style.background = S.catCss[e.cat];
        T.m.appendChild(dot2);
        meta = isSeq(n) ? ('이미지 시퀀스' + (num(n.count) > 1 ? ' · ' + num(n.count).toLocaleString('ko-KR') + '프레임' : '')) : '파일';
      } else if (e.t === 'other') {
        meta = '크기가 작은 항목을 묶어서 보여 드려요';
      } else if (e.t === 'rest') {
        meta = '너무 작아서 한 칸으로 묶었어요';
        if (e.parent && drillable(e.parent.n)) hint = '클릭하면 「' + midTrunc(dispName(e.parent.n), 24) + '」 안으로 들어가요';
      } else if (e.t === 'loose') {
        meta = '이 폴더에 바로 들어 있는 작은 파일들이에요';
        if (num(n.files) > 0) meta += ' · ' + num(n.files).toLocaleString('ko-KR') + '개';
      }
      var mt = num(n.mtime) || num(n.newest);
      if (mt && (e.t === 'file' || e.t === 'group' || e.t === 'dir')) meta += ' · ' + relTime(mt) + ' 수정';
      T.m.appendChild(doc.createTextNode(meta));
      T.m.hidden = !meta;
      T.h.textContent = hint;
      T.h.hidden = !hint;
      S.tipW = tip.offsetWidth;
      S.tipH = tip.offsetHeight;
    }

    // SPEC §2 상대 시간 (mtime 은 epoch 초)
    function relTime(sec) {
      var d = Date.now() / 1000 - sec;
      if (d < 60) return '방금';
      if (d < 3600) return Math.floor(d / 60) + '분 전';
      if (d < 86400) return Math.floor(d / 3600) + '시간 전';
      var days = Math.floor(d / 86400);
      if (days < 2) return '어제';
      if (days < 7) return days + '일 전';
      if (days < 30) return Math.floor(days / 7) + '주 전';
      if (days < 365) return Math.max(1, Math.floor(days / 30)) + '개월 전';
      return Math.floor(days / 365) + '년 전';
    }

    function placeTip(cx, cy) {
      var vw = win.innerWidth || 0, vh = win.innerHeight || 0, w = S.tipW, h = S.tipH;
      var x = cx + 14, y = cy + 18;
      if (x + w > vw - 8) x = cx - 14 - w;
      if (y + h > vh - 8) y = cy - 14 - h;
      x = Math.max(8, x); y = Math.max(8, y);
      tip.style.transform = 'translate(' + Math.round(x) + 'px,' + Math.round(y) + 'px)';
    }

    function onMoveFrame() {
      S.moveQueued = false;
      if (S.dead) return;
      if (S.busy || !S.scene || !S.inside) { setHover(null); return; }
      var r = root.getBoundingClientRect();
      var e = hitTest(S.scene, S.mx - r.left, S.my - r.top);
      setHover(e);
      if (e) { if (!S.tipShown) showTip(true); placeTip(S.mx, S.my); }
    }
    function queueMove() {
      if (S.moveQueued) return;
      S.moveQueued = true;
      win.requestAnimationFrame(onMoveFrame);
    }

    // ------------------------------------------------ 애니메이션

    function reduced() {
      try { return !!(win.matchMedia && win.matchMedia('(prefers-reduced-motion: reduce)').matches); } catch (e) { return false; }
    }
    function motionOK() { return !reduced() && !doc.hidden; }

    // alive() 가 거짓이 되면 그 자리에서 멈춘다. 숨은 탭에서 rAF 가 멈춰도 시한 타이머가 끝을 보장한다.
    function tween(ms, frame, alive) {
      if (!(ms > 0) || !motionOK()) { if (alive()) { S.frame = frame; S.frameT = 1; frame(1); } return Promise.resolve(); }
      return new Promise(function (res) {
        var t0 = -1, done = false, raf = 0, guard = 0;
        function finish(run) {
          if (done) return;
          done = true;
          win.cancelAnimationFrame(raf);
          win.clearTimeout(guard);
          if (run && alive()) { S.frame = frame; S.frameT = 1; frame(1); }
          res();
        }
        function step(now) {
          if (done) return;
          if (!alive()) { finish(false); return; }
          if (t0 < 0) t0 = now;
          var t = Math.min(1, (now - t0) / ms);
          S.frame = frame; S.frameT = t;
          frame(t);
          if (t >= 1) finish(false); else raf = win.requestAnimationFrame(step);
        }
        raf = win.requestAnimationFrame(step);
        guard = win.setTimeout(function () { finish(true); }, ms + 300);
      });
    }

    function snap(dst) {
      var g = dst.getContext('2d');
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.globalAlpha = 1;
      g.drawImage(base, 0, 0);
    }

    // 장면 좌표 focus 를 화면 사각형 scr(css px) 에 보이게 하는 카메라
    function camFor(sc, focus, scr) {
      var kx = scr.w / focus.w, ky = scr.h / focus.h;
      return { x: focus.x - scr.x / kx, y: focus.y - scr.y / ky, w: S.W / kx, h: S.H / ky };
    }
    function toScreen(sc, r) {
      var kx = S.W / (sc.W || 1), ky = S.H / (sc.H || 1);
      return { x: r.x * kx, y: r.y * ky, w: r.w * kx, h: r.h * ky };
    }
    function focusOf(e) {
      if (e.t === 'group') return { x: e.cx, y: e.cy, w: e.cw, h: e.ch };
      var g = e.gap / 2;
      return { x: e.x + g, y: e.y + g, w: Math.max(1, e.w - e.gap), h: Math.max(1, e.h - e.gap) };
    }

    // ------------------------------------------------ 상태·탐색

    function setState(s) { root.dataset.state = s; }
    function setLoading(on) {
      if (on) root.setAttribute('data-loading', ''); else root.removeAttribute('data-loading');
    }
    function showMsg(kind, text, retryId) {
      msg.innerHTML = '';
      if (!kind) { msg.classList.remove('show'); return; }
      var ic = doc.createElement('div');
      ic.innerHTML = kind === 'error' ? ICON_ERR : ICON_EMPTY;
      msg.appendChild(ic.firstChild);
      var t = doc.createElement('div');
      t.textContent = text;
      msg.appendChild(t);
      if (kind === 'error' && retryId !== undefined) {
        var b = doc.createElement('button');
        b.type = 'button';
        b.textContent = '다시 시도';
        b.addEventListener('click', function () { open(retryId); });
        msg.appendChild(b);
      }
      msg.classList.add('show');
    }
    function errText(err) {
      var m = err && (typeof err === 'string' ? err : err.message);
      return (typeof m === 'string' && m.trim() && m.length <= 140) ? m.trim() : '폴더를 불러오지 못했어요';
    }
    function flash(text) {
      toast.textContent = text;
      toast.classList.add('show');
      win.clearTimeout(S.toastTimer);
      S.toastTimer = win.setTimeout(function () { toast.classList.remove('show'); }, 2600);
    }

    function crumbOf(n) { return { id: n.id, name: dispName(n) }; }
    function crumbsFrom(n) {
      if (Array.isArray(n.crumbs) && n.crumbs.length) {
        var out = [];
        for (var i = 0; i < n.crumbs.length; i++) {
          var c = n.crumbs[i] || {};
          out.push({ id: c.id !== undefined ? c.id : c.dir, name: String(c.name || c.path || '') || '루트' });
        }
        if (key(out[out.length - 1].id) !== key(n.id)) out.push(crumbOf(n));
        return out;
      }
      return [crumbOf(n)];
    }
    function emitPath() {
      if (typeof opts.onPath !== 'function') return;
      var c = S.stack.map(function (x) { return { id: x.id, name: x.name }; });
      try { opts.onPath(c); } catch (e) { win.setTimeout(function () { throw e; }); }
    }
    function call(fn, a, b, c) {
      if (typeof fn !== 'function') return;
      try { fn(a, b, c); } catch (e) { win.setTimeout(function () { throw e; }); }
    }

    // 위로 갈 때 바로 그릴 수 있게 현재 경로의 조상만 들고 있는다(깊이 2 트리는 클 수 있다)
    function cachePut(n) {
      var k = key(n.id);
      if (!k) return;
      S.cache.delete(k);
      S.cache.set(k, n);
      var keep = {};
      for (var i = 0; i < S.stack.length; i++) keep[key(S.stack[i].id)] = 1;
      keep[k] = 1;
      S.cache.forEach(function (v, ck) { if (!keep[ck]) S.cache.delete(ck); });
    }

    function safeLoad(id) {
      var p;
      try { p = Promise.resolve(opts.load(id)); } catch (err) { p = Promise.reject(err); }
      return p.then(function (r) {
        var n = r;
        if (r && typeof r === 'object' && r.node && typeof r.node === 'object' && !Array.isArray(r.children)) n = r.node;
        if (!n || typeof n !== 'object' || Array.isArray(n)) throw new Error('트리 데이터 형식이 올바르지 않아요');
        return { ok: true, node: n };
      }).catch(function (err) { return { ok: false, err: err }; });
    }

    function waitLoad(p, tok, delay) {
      var tm = win.setTimeout(function () { if (alive(tok)) setLoading(true); }, delay);
      return p.then(function (r) {
        win.clearTimeout(tm);
        if (alive(tok)) setLoading(false);
        return r;
      });
    }

    function alive(tok) { return !S.dead && tok === S.tok; }
    function beginNav() {
      S.tok++;
      S.busy = true;
      S.frame = null;
      setHover(null);
      setState(S.scene ? 'busy' : 'loading');
      return S.tok;
    }
    function endNav(tok) {
      if (!alive(tok)) return;
      S.busy = false;
      S.frame = null;
      setLoading(false);
      if (S.scene && (S.scene.W !== S.W || S.scene.H !== S.H)) {
        S.scene = buildScene(S.node, S.W, S.H);
        renderStatic();
      }
      if (S.scene) setState(S.scene.list.length ? 'ready' : 'empty');
      if (S.inside) queueMove();
    }

    function setNode(n, sc) {
      S.node = n;
      S.scene = sc && sc.node === n && sc.W === S.W && sc.H === S.H ? sc : buildScene(n, S.W, S.H);
      var last = S.stack[S.stack.length - 1], nm = dispName(n);
      if (last && key(last.id) === key(n.id)) {
        if (last.name !== nm) { last.name = nm; emitPath(); }
      } else { S.stack = crumbsFrom(n); emitPath(); }
      cachePut(n);
      if (!kidsOf(n).length) showMsg('empty', num(n.size) > 0 ? '표시할 만큼 큰 항목이 없어요' : '이 폴더는 비어 있어요');
      else showMsg(null);
      root.setAttribute('aria-label', '트리맵: ' + dispName(n));
    }

    // 새 장면을 그리고, 직전 화면(snapA)을 그 위에서 서서히 걷어 낸다
    function crossTo(n, tok, ms, sc) {
      snap(snapA);
      setNode(n, sc);
      renderStatic(sctx);
      var gen = S.gen;
      return tween(ms, function (t) {
        if (S.gen !== gen) { renderStatic(); return; }   // 도중에 크기가 바뀌면 스냅숏이 지워진다
        bctx.setTransform(1, 0, 0, 1, 0, 0);
        bctx.globalAlpha = 1;
        bctx.drawImage(snapB, 0, 0);
        if (t < 1) {
          bctx.globalAlpha = 1 - EASE(t);
          bctx.drawImage(snapA, 0, 0);
          bctx.globalAlpha = 1;
        }
      }, function () { return alive(tok); }).then(function () {
        if (alive(tok)) renderStatic();
      });
    }

    function drill(e) {
      if (S.dead || S.busy || !S.scene) return Promise.resolve(false);
      if (e.t === 'rest' && e.parent) e = e.parent;
      if (!(e.t === 'group' || e.t === 'dir') || !drillable(e.n)) return Promise.resolve(false);
      var n = e.n, sc = S.scene;
      var tok = beginNav(), prev = S.stack.slice();
      var add = e.depth === 2 && e.parent ? [crumbOf(e.parent.n), crumbOf(n)] : [crumbOf(n)];
      S.stack = S.stack.concat(add);
      emitPath();
      var pending = safeLoad(n.id);
      var focus = focusOf(e), s0 = toScreen(sc, focus), s1 = { x: 0, y: 0, w: S.W, h: S.H };
      return tween(ZOOM_MS, function (t) {
        var k = EASE(t), cam = camFor(sc, focus, lerpR(s0, s1, k));
        paint(bctx, sc, cam, { labels: 1 - Math.min(1, t / 0.22), focus: focus, fade: k, lite: t > 0 && sc.list.length > LITE_N });
      }, function () { return alive(tok); }).then(function () {
        if (!alive(tok)) return false;
        return waitLoad(pending, tok, SHIMMER_DELAY).then(function (r) {
          if (!alive(tok)) return false;
          if (!r.ok) {
            S.stack = prev;
            emitPath();
            flash(errText(r.err));
            return crossTo(S.node, tok, FADE_MS, sc).then(function () { endNav(tok); return false; });
          }
          return crossTo(r.node, tok, FADE_MS).then(function () { endNav(tok); return true; });
        });
      });
    }

    function findIn(sc, n) {
      var k = key(n.id), best = null;
      for (var i = 0; i < sc.list.length; i++) {
        var e = sc.list[i];
        if ((e.t === 'group' || e.t === 'dir') && key(e.n.id) === k) {
          if (e.depth === 1) return e;
          if (!best) best = e;
        }
      }
      return best;
    }

    function parentOf() {
      if (S.stack.length >= 2) return S.stack[S.stack.length - 2].id;
      var p = S.node && S.node.parent;
      if (p !== undefined && p !== null && p !== -1 && p !== '' && key(p) !== key(S.node.id)) return p;
      return undefined;
    }

    function up() {
      if (S.dead || S.busy || !S.node) return Promise.resolve(false);
      var pid = parentOf();
      if (pid === undefined) return Promise.resolve(false);
      var cur = S.node, tok = beginNav(), prev = S.stack.slice();
      S.stack = S.stack.length >= 2 ? S.stack.slice(0, -1) : [{ id: pid, name: '' }];
      emitPath();
      var pending = safeLoad(pid);
      var cached = S.cache.get(key(pid));
      var got = cached ? Promise.resolve({ ok: true, node: cached, cached: true }) : waitLoad(pending, tok, SHIMMER_DELAY);
      return got.then(function (r) {
        if (!alive(tok)) return false;
        if (!r.ok) {
          S.stack = prev;
          emitPath();
          flash(errText(r.err));
          endNav(tok);
          return false;
        }
        var pn = r.node, psc = buildScene(pn, S.W, S.H), fe = findIn(psc, cur);
        var anim;
        if (fe && motionOK()) {
          snap(snapA);
          var gen = S.gen;
          var focus = focusOf(fe), s0 = { x: 0, y: 0, w: S.W, h: S.H }, s1 = toScreen(psc, focus), dpr = S.dpr;
          anim = tween(ZOOM_MS, function (t) {
            var k = EASE(t), scr = lerpR(s0, s1, k), cam = camFor(psc, focus, scr);
            paint(bctx, psc, cam, { labels: Math.max(0, (t - 0.62) / 0.38), focus: focus, fade: 1 - k, lite: t < 1 && psc.list.length > LITE_N });
            var a = 1 - Math.min(1, t / 0.32);
            if (a > 0 && S.gen === gen) {
              bctx.globalAlpha = EASE(a);
              bctx.drawImage(snapA, 0, 0, snapA.width, snapA.height, scr.x * dpr, scr.y * dpr, scr.w * dpr, scr.h * dpr);
              bctx.globalAlpha = 1;
            }
          }, function () { return alive(tok); }).then(function () {
            if (!alive(tok)) return;
            setNode(pn, psc);
            renderStatic();
          });
        } else anim = crossTo(pn, tok, FADE_MS, psc);
        return anim.then(function () {
          if (!alive(tok)) return false;
          endNav(tok);
          if (r.cached) {
            pending.then(function (f) {
              if (S.dead || !f.ok || !S.node || key(f.node.id) !== key(pn.id)) return;
              if (S.node !== pn) return;
              if (S.busy) { cachePut(f.node); return; }
              if (sig(f.node) === sig(pn)) { cachePut(f.node); return; }
              var t2 = beginNav();
              crossTo(f.node, t2, FADE_MS).then(function () { endNav(t2); });
            });
          }
          return true;
        });
      });
    }

    function refresh() {
      var tok = beginNav(), id = S.node.id;
      return waitLoad(safeLoad(id), tok, 300).then(function (r) {
        if (!alive(tok)) return false;
        if (!r.ok) { flash(errText(r.err)); endNav(tok); return false; }
        if (sig(r.node) === sig(S.node)) { cachePut(r.node); endNav(tok); return true; }
        return crossTo(r.node, tok, FADE_MS).then(function () { endNav(tok); return true; });
      });
    }

    function open(dirId) {
      if (S.dead) return Promise.resolve(false);
      var wasBusy = S.busy;
      if (wasBusy) { S.tok++; S.busy = false; }
      readTheme();
      MEAS.clear();
      if (S.W <= 0) measure();
      var k = key(dirId);
      if (!wasBusy && S.node && S.scene && key(S.node.id) === k) return refresh();
      var idx = -1;
      for (var i = 0; i < S.stack.length; i++) if (key(S.stack[i].id) === k) { idx = i; break; }
      if (!wasBusy && S.scene) {
        if (idx >= 0 && idx === S.stack.length - 2) return up();
        if (idx < 0) {
          var hit = null;
          for (var j = 0; j < S.scene.list.length; j++) {
            var e = S.scene.list[j];
            if ((e.t === 'group' || e.t === 'dir') && drillable(e.n) && key(e.n.id) === k) { hit = e; if (e.depth === 1) break; }
          }
          if (hit) return drill(hit);
        }
      }
      var tok = beginNav(), prev = S.stack.slice(), had = !!S.scene;
      if (idx >= 0) { S.stack = S.stack.slice(0, idx + 1); emitPath(); }
      if (!had) { showMsg(null); setLoading(true); renderStatic(); }
      return waitLoad(safeLoad(dirId), tok, had ? SHIMMER_DELAY : 0).then(function (r) {
        if (!alive(tok)) return false;
        if (!r.ok) {
          if (idx >= 0) { S.stack = prev; emitPath(); }
          if (!had) {
            S.busy = false;
            setState('error');
            setLoading(false);
            renderStatic();
            showMsg('error', errText(r.err), dirId);
            return false;
          }
          flash(errText(r.err));
          endNav(tok);
          return false;
        }
        if (idx < 0) S.stack = crumbsFrom(r.node);
        var p = crossTo(r.node, tok, had ? FADE_MS : FIRST_MS);
        if (idx < 0) emitPath();
        return p.then(function () {
          endNav(tok);
          return true;
        });
      });
    }

    // ------------------------------------------------ 하이라이트

    // 새 상태를 한 번만 그려 두고 직전 화면에서 교차 페이드한다(칸 수와 무관하게 프레임당 비용이 일정)
    function setHighlight(cat) {
      if (S.dead) return;
      var c = normCat(cat);
      if (c === S.hl) return;
      S.hl = c;
      var sc = S.scene;
      if (!sc) return;
      hlTargets(sc, c);
      hlSnap(sc);
      if (S.busy) return;
      var my = ++S.hlTok;
      if (!motionOK()) { renderStatic(); return; }
      snap(snapA);
      renderStatic(sctx);
      var gen = S.gen;
      tween(HL_MS, function (t) {
        if (S.gen !== gen) { renderStatic(); return; }
        bctx.setTransform(1, 0, 0, 1, 0, 0);
        bctx.globalAlpha = 1;
        bctx.drawImage(snapB, 0, 0);
        if (t < 1) {
          bctx.globalAlpha = 1 - EASE(t);
          bctx.drawImage(snapA, 0, 0);
          bctx.globalAlpha = 1;
        }
      }, function () { return !S.dead && my === S.hlTok && !S.busy && S.scene === sc; });
    }

    // ------------------------------------------------ 크기

    function measure() {
      var w = root.clientWidth, h = root.clientHeight;
      var dpr = Math.max(1, Math.min(4, win.devicePixelRatio || 1));
      if (w === S.W && h === S.H && dpr === S.dpr) return false;
      var dprChanged = dpr !== S.dpr;
      S.W = w; S.H = h; S.dpr = dpr;
      var dw = Math.max(1, Math.round(w * dpr)), dh = Math.max(1, Math.round(h * dpr));
      S.gen++;
      base.width = over.width = snapA.width = snapB.width = dw;
      base.height = over.height = snapA.height = snapB.height = dh;
      S.hatch = null;
      if (dprChanged) { MEAS.clear(); watchDpr(); }
      return true;
    }
    function relayout(force) {
      if (S.dead) return;
      var changed = measure();
      if (!changed && !force) return;
      if (S.busy) {
        if (S.frame) S.frame(S.frameT || 0);
        else renderStatic();
        return;
      }
      S.hover = null;
      if (S.node) {
        S.scene = buildScene(S.node, S.W, S.H);
        if (root.dataset.state === 'ready' || root.dataset.state === 'empty') setState(S.scene.list.length ? 'ready' : 'empty');
      }
      renderStatic();
      if (S.inside) queueMove();
    }
    function scheduleResize() {
      if (S.rafResize) return;
      S.rafResize = win.requestAnimationFrame(function () { S.rafResize = 0; relayout(false); });
    }
    function watchDpr() {
      if (!win.matchMedia) return;
      try {
        if (S.mq && S.mqf) S.mq.removeEventListener('change', S.mqf);
        S.mq = win.matchMedia('(resolution: ' + (win.devicePixelRatio || 1) + 'dppx)');
        S.mqf = function () { scheduleResize(); };
        S.mq.addEventListener('change', S.mqf);
      } catch (e) { /* 구형 */ }
    }

    var ro = null;
    if (win.ResizeObserver) { ro = new win.ResizeObserver(scheduleResize); ro.observe(root); }
    else win.addEventListener('resize', scheduleResize);

    // ------------------------------------------------ 입력

    function hitAt(ev) {
      if (!S.scene) return null;
      var r = root.getBoundingClientRect();
      return hitTest(S.scene, ev.clientX - r.left, ev.clientY - r.top);
    }
    function onMove(ev) { S.mx = ev.clientX; S.my = ev.clientY; S.inside = true; queueMove(); }
    function onLeave() { S.inside = false; setHover(null); }
    function onDown() { showTip(false); }
    function onClick(ev) {
      if (S.busy || !S.scene || ev.button !== 0) return;
      var e = hitAt(ev);
      if (!e) return;
      if (e.t === 'group' || e.t === 'dir' || e.t === 'rest') { drill(e); return; }
      if (e.t === 'file') call(opts.onOpenItem, e.n);
    }
    function onCtx(ev) {
      ev.preventDefault();
      if (S.busy || !S.scene) return;
      var e = hitAt(ev);
      // 묶음('other'·'rest')과 'loose'(경로가 부모 폴더)는 조작 대상이 아니다
      if (!e || e.t === 'other' || e.t === 'rest' || e.t === 'loose' || !e.n.path) return;
      showTip(false);
      call(opts.onContext, e.n, ev.clientX, ev.clientY);
    }
    root.addEventListener('pointermove', onMove, { passive: true });
    root.addEventListener('pointerleave', onLeave, { passive: true });
    root.addEventListener('pointerdown', onDown, { passive: true });
    root.addEventListener('click', onClick);
    root.addEventListener('contextmenu', onCtx);

    // 캔버스는 @font-face 를 스스로 받지 않으므로 직접 요청하고, 받으면 다시 그린다
    function onFonts() { if (S.dead) return; MEAS.clear(); if (S.scene) for (var i = 0; i < S.scene.list.length; i++) S.scene.list[i].lab = null; if (!S.busy) renderStatic(); }
    readTheme();
    if (doc.fonts && doc.fonts.load) {
      var fam = S.theme.font, ps = [];
      ['500 11px ', '600 12px ', '700 12px '].forEach(function (f) { ps.push(doc.fonts.load(f + fam, '가A1').catch(function () { return null; })); });
      Promise.all(ps).then(onFonts);
      if (doc.fonts.addEventListener) doc.fonts.addEventListener('loadingdone', onFonts);
    }
    setupTnum();
    measure();
    watchDpr();
    renderStatic();

    function destroy() {
      if (S.dead) return;
      S.dead = true;
      S.tok++;
      if (ro) ro.disconnect(); else win.removeEventListener('resize', scheduleResize);
      if (S.mq && S.mqf) { try { S.mq.removeEventListener('change', S.mqf); } catch (e) { /* 무시 */ } }
      if (doc.fonts && doc.fonts.removeEventListener) doc.fonts.removeEventListener('loadingdone', onFonts);
      if (S.tnumFace && doc.fonts && doc.fonts.delete) { try { doc.fonts.delete(S.tnumFace); } catch (e) { /* 무시 */ } }
      if (S.rafResize) win.cancelAnimationFrame(S.rafResize);
      win.clearTimeout(S.toastTimer);
      root.removeEventListener('pointermove', onMove);
      root.removeEventListener('pointerleave', onLeave);
      root.removeEventListener('pointerdown', onDown);
      root.removeEventListener('click', onClick);
      root.removeEventListener('contextmenu', onCtx);
      if (tip.parentNode) tip.parentNode.removeChild(tip);
      if (root.parentNode) root.parentNode.removeChild(root);
      S.scene = null; S.node = null; S.cache.clear(); MEAS.clear();
    }

    return {
      open: function (dirId) { return open(dirId); },
      up: function () { return up(); },
      resize: function () { if (S.dead) return; readTheme(); MEAS.clear(); if (S.scene) for (var i = 0; i < S.scene.list.length; i++) S.scene.list[i].lab = null; relayout(true); },
      destroy: destroy,
      setHighlight: setHighlight
    };
  }

  global.Treemap = { create: create };
})(typeof window !== 'undefined' ? window : this);
