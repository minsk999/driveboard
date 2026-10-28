// 영상팀 파일명 규칙(BRC) 파서 — 참조 구현(차등 시험용, 수정 금지).
// 출처: BannerNode index.html 의 BRC 블록(원본: 배치리네이머 hostscript.jsx). 규칙 순서:
//   날짜_광고주_캠페인_페이지p_작업내용_해상도_작업자_@작업종류_작업구분
// core/projects.py 의 파이썬 이식본이 이 함수와 같은 답을 내는지 tests 가 node 로 대조한다.
const BRC=(function(){
  var _RES_LIST = ["1920x1080","1080x1080","1080x1920","1080x1350",
                   "1200x628","1200x1200","1200x1500",
                   "1440x1440","1440x1800","3840x2160","2160x3840"];
  var _JOBKIND_CODES = ["@MB","@BC","@SZ","@ED","@AV","@AB","@MV","@SH","@CP"];
  
  var _WORKER_LIST = ["KMS","HYC","JJH","PNJ","KBH","GJH"];
  function _isWorkerInitial(p) {
      p = String(p||"").toUpperCase();
      for (var i=0;i<_WORKER_LIST.length;i++) if (p===_WORKER_LIST[i]) return true;
      return false;
  }
  function _stripNum(name) { return String(name||"").replace(/\s+\d+$/,""); }
  function _isDate(p) { return /^\d{6}$/.test(String(p)); }
  function _isRes(p) {
      // 목록 기반 + 패턴 기반 (커스텀 해상도 포함)
      if (/^\d{3,4}[xX]\d{3,4}$/.test(String(p))) return true;
      for (var i=0;i<_RES_LIST.length;i++) if (p===_RES_LIST[i]) return true;
      return false;
  }
  function _isWorkType(p) {
      p = String(p||"");
      return p==="xian"||p==="vari"||p==="sf"||/^retuch\d+$/.test(p);
  }
  function _isJobKind(p) {
      p = String(p||"");
      for (var i=0;i<_JOBKIND_CODES.length;i++) if (p===_JOBKIND_CODES[i]) return true;
      return false;
  }
  function _isSpecial(p) {
      return _isDate(p)||_isRes(p)||_isWorkType(p)||_isJobKind(p)||/^\d+p$/.test(p);
  }
  function _smartSplit(name) {
      var raw = _stripNum(name).split("_");
      var merged = [];
      var i = 0;
      while (i < raw.length) {
          var p = raw[i];
          if (i+1 < raw.length && /^\d+$/.test(raw[i+1]) && !_isSpecial(p)) {
              merged.push(p + "_" + raw[i+1]);
              i += 2;
          } else {
              merged.push(p);
              i += 1;
          }
      }
      return merged;
  }
  function _tailIdx(parts) {
      for (var t = 0; t < parts.length; t++) {
          if (_isJobKind(parts[t]) || _isWorkType(parts[t])) return t;
      }
      return parts.length;
  }
  function _findWorkerIdx(parts) {
      var i, g, q;
      // 1순위: @작업종류 바로 앞의 대문자
      for (i = 1; i < parts.length; i++) {
          if (_isJobKind(parts[i]) && /^[A-Z]{2,5}$/.test(parts[i-1])) return i - 1;
      }
      // 특수 토큰(날짜·해상도·작업종류·작업구분·페이지)을 뺀 '일반' 토큰 위치
      var gIdx = [];
      for (g = 0; g < parts.length; g++) if (parts[g] && !_isSpecial(parts[g])) gIdx.push(g);
      // 2순위: 팀원 이니셜 목록에 있는 것. 0번은 광고주 자리라 제외한다.
      for (q = gIdx.length - 1; q >= 1; q--) {
          if (_isWorkerInitial(parts[gIdx[q]])) return gIdx[q];
      }
      // 3순위: 일반 토큰이 3개 이상일 때만 마지막 대문자 토큰을 작업자로 본다.
      // 3개 미만이면 광고주·캠페인뿐이라 작업자가 있을 자리가 없다.
      if (gIdx.length >= 3) {
          var lastG = gIdx[gIdx.length - 1];
          if (/^[A-Z]{2,5}$/.test(parts[lastG])) return lastG;
      }
      return -1;
  }
  function _applyPage(name, v) {
      var parts = _stripNum(name).split("_");
      var token = v + "p";
      // 이미 페이지 있으면 교체
      for (var i=0;i<parts.length;i++) {
          if (/^\d+p$/.test(parts[i])) { parts[i]=token; return parts.join("_"); }
      }
      // 없으면 generic[2] 앞(캠페인명 다음, 작업내용 앞)에 삽입
      var genericCount = 0;
      for (var j=0;j<parts.length;j++) {
          if (!_isSpecial(parts[j])) {
              genericCount++;
              if (genericCount === 2) {
                  // generic[1] 다음 위치 = generic[2] 앞
                  parts.splice(j+1, 0, token);
                  return parts.join("_");
              }
          }
      }
      // generic이 2개 미만이면 끝에 삽입
      parts.push(token);
      return parts.join("_");
  }
  function _applyWorker(name, v) {
      var parts = _stripNum(name).split("_");
      var at = _findWorkerIdx(parts);
      if (at >= 0) { parts[at] = v; return parts.join("_"); }
      // 못 찾으면 교체가 아니라 삽입한다.
      // 예전엔 두 번째 루프가 뒤에서부터 아무 대문자나 잡아 덮어썼다. 주석에는
      // "no @jobKind" 라고 적혀 있었지만 실제로는 else 가 아니라 fall-through 라,
      // 즉 @작업종류가 있어도 그 앞이 대문자가 아니면 이 루프로 떨어졌다.
      at = _tailIdx(parts);
      parts.splice(at, 0, v);
      return parts.join("_");
  }
  function _applyJobKind(name, v) {
      var parts = _stripNum(name).split("_");
      for (var i=0;i<parts.length;i++) {
          if (_isJobKind(parts[i])) { parts[i]=v; return parts.join("_"); }
      }
      var idx = (parts.length>=1&&_isWorkType(parts[parts.length-1]))?parts.length-1:parts.length;
      parts.splice(idx,0,v);
      return parts.join("_");
  }
  function _applyWorkType(name, v) {
      var parts = _stripNum(name).split("_");
      var isRetuch = /^retuch\d+$/.test(v);
      for (var i=0;i<parts.length;i++) {
          if (_isWorkType(parts[i])) {
              if (isRetuch && /^retuch(\d+)$/.test(parts[i])) {
                  var m = parts[i].match(/^retuch(\d+)$/);
                  parts[i] = "retuch" + (parseInt(m[1],10)+1);
              } else {
                  parts[i] = v;
              }
              return parts.join("_");
          }
      }
      parts.push(v);
      return parts.join("_");
  }
  function _parseCompName(name) {
      // worker 를 빠뜨리면 안 된다. 불러오기 -> 이름변경 왕복에서 원작업자 이니셜이
      // 조작자 것으로 조용히 바뀐다 (KMS 가 만든 컴프를 PNJ 가 열어 페이지만 고쳐도
      // 이름의 KMS 가 PNJ 로 바뀐다).
      var result = { client:"",campaign:"",page:"",task:"",resolution:"",worker:"",jobKind:"",workType:"" };
      if (!name) return result;
      var parts = _smartSplit(name);
      var generic = [];
  
      var workerIdx = _findWorkerIdx(parts);
  
      for (var j=0;j<parts.length;j++) {
          var p = parts[j];
          if (!p) continue;
          if (_isDate(p)) continue;
          if (_isRes(p))      { result.resolution = p.replace(/X/g, "x"); continue; }
          if (_isWorkType(p)) { result.workType = /^retuch\d+$/.test(p) ? "retuch1" : p; continue; }
          if (_isJobKind(p))  { result.jobKind = p; continue; }
          if (/^\d+p$/.test(p)) { result.page = p.replace(/p$/,""); continue; }
          if (j === workerIdx) { result.worker = p; continue; } // skip worker
          generic.push(p);
      }
  
      if (generic.length>0) result.client   = generic[0];
      if (generic.length>1) result.campaign = generic[1];
      if (generic.length>2) result.task     = generic.slice(2).join("_");
      return result;
  }
  return { RES:_RES_LIST, JOBKIND:_JOBKIND_CODES, WORKER:_WORKER_LIST,
    isDate:_isDate, isRes:_isRes, isWorkType:_isWorkType, isJobKind:_isJobKind, isSpecial:_isSpecial,
    isWorkerInitial:_isWorkerInitial, stripNum:_stripNum, smartSplit:_smartSplit,
    tailIdx:_tailIdx, findWorkerIdx:_findWorkerIdx,
    applyPage:_applyPage, applyWorker:_applyWorker, applyJobKind:_applyJobKind, applyWorkType:_applyWorkType,
    parse:_parseCompName };
})();
if (typeof module !== 'undefined') module.exports = BRC;
