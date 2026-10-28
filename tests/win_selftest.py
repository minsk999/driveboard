"""Windows 실기 자가 점검 — 사용자가 Windows 에서 직접 실행한다.

    python tests\\win_selftest.py            (탐색기 창 열기 검사는 --reveal 을 붙이면 함께)
    python tests\\win_selftest.py --nas ORICO   (NAS 공유 목록 조회도 — 장치 이름이나 IP. 값 없이 --nas 면 물어본다)

이 스크립트는 **자기가 만든 파일만** 다룬다.
- %TEMP% 에 'DriveBoard-selftest-<id>' 이름의 파일·폴더를 만들어 휴지통으로 보내 본다(휴지통에 남는다).
- 고정 드라이브가 둘 이상이면 두 번째 드라이브 최상위에 'DriveBoard-selftest-<id>' 폴더를 만들어
  이동 → 되돌리기를 해 보고, 끝나면 그 폴더와 %TEMP% 의 폴더를 지운다.
- 같은 방식으로 복사 → 되돌리기(복사본만 휴지통으로)도 해 본다. 원본은 그대로 남아야 한다.
- 외장·USB·NAS 는 목록·종류·휴지통 유무만 읽는다(그 장치에는 아무것도 쓰지 않는다).
  --nas 를 주면 그 장치의 공유 목록과 첫 공유의 용량을 읽기만 한다.
- 휴지통 비우기는 하지 않는다(영구 삭제라서).
- 결과 표의 PASS/FAIL 을 그대로 알려 주면 된다. 종료 코드: 0=모두 통과, 1=실패 있음, 2=Windows 아님.
"""
import ctypes
import hashlib
import os
import shutil
import sys
import tempfile
import time
import traceback
import unicodedata
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RUN_ID = uuid.uuid4().hex[:10]
TAG = "DriveBoard-selftest-" + RUN_ID
OLD = 1577836800   # 2020-01-01
rows = []          # (항목, 결과, 내용)
created = []       # 끝나고 지울 우리 폴더(절대 경로)


def add(name, result, detail=""):
    rows.append((name, result, detail))
    print("  [%s] %s %s" % (result, name, ("— " + detail) if detail else ""), flush=True)


def run(name, fn):
    try:
        res = fn()
        if res is None:
            return
        if isinstance(res, tuple):
            add(name, "PASS" if res[0] is True else ("FAIL" if res[0] is False else res[0]), res[1])
    except Exception as e:  # noqa: BLE001
        add(name, "FAIL", "%s: %s" % (type(e).__name__, e))
        traceback.print_exc()


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def pad(s, w):
    return s + " " * max(0, w - width(s))


def human(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return ("%.2f%s" % (n, u)) if u != "B" else ("%d%s" % (n, u))
        n /= 1024


def xp(p):
    """우리가 만든 깊은 경로를 다루기 위한 확장 길이 경로."""
    p = os.path.abspath(p)
    return p if p.startswith("\\\\?\\") else "\\\\?\\" + p


def sha(p):
    h = hashlib.sha256()
    with open(xp(p), "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def snapshot(top):
    out = {}
    base = xp(top)
    n = len(base) + 1
    for dp, dns, fns in os.walk(base):
        for d in dns:
            out[os.path.join(dp, d)[n:]] = ("d",)
        for f in fns:
            p = os.path.join(dp, f)
            st = os.stat(p)
            out[p[n:]] = ("f", st.st_size, int(st.st_mtime), sha(p[4:]))
    return out


def mkdirs(path):
    """한 단계씩 만든다(긴 경로에서 os.makedirs 의 버전별 차이를 피한다)."""
    path = os.path.abspath(path)
    todo = []
    while not os.path.isdir(xp(path)):
        todo.append(path)
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    for d in reversed(todo):
        os.mkdir(xp(d))


def mkfile(path, size, mtime=OLD):
    mkdirs(os.path.dirname(path))
    with open(xp(path), "wb") as f:
        f.write(os.urandom(size))
    os.utime(xp(path), (mtime, mtime))
    return path


def remove_ours(path):
    """우리 표식(TAG)이 이름에 있는 폴더·파일만 지운다."""
    if TAG not in os.path.basename(os.path.normpath(path)):
        print("  ! 표식이 없는 경로라 지우지 않았어요: %s" % path)
        return False
    p = xp(path)
    if not os.path.lexists(p):
        return True
    if os.path.isdir(p) and not os.path.islink(p):
        shutil.rmtree(p)
    else:
        os.remove(p)
    return not os.path.lexists(p)


def wait(q, jid, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = q.get(jid)
        if j and j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise TimeoutError("작업이 %d초 안에 끝나지 않았어요" % timeout)


def main():
    if os.name != "nt":
        print("이 검사는 Windows 에서만 돌아가요.")
        return 2
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    data_home = tempfile.mkdtemp(prefix=TAG + "-home-")
    created.append(data_home)
    os.environ["DRIVEBOARD_HOME"] = data_home
    os.environ.pop("DRIVEBOARD_FAKE_ADOBE", None)
    from core import ops, safety, winapi
    winapi.set_fake_root(None)          # DRIVEBOARD_FAKE 가 잡혀 있어도 실제 API 로

    print("DriveBoard Windows 자가 점검 (%s, Python %s %d비트)\n" %
          (RUN_ID, sys.version.split()[0], ctypes.sizeof(ctypes.c_void_p) * 8))

    # 1. 구조체
    def t_struct():
        is64 = ctypes.sizeof(ctypes.c_void_p) == 8
        a, b = ctypes.sizeof(winapi.SHFILEOPSTRUCTW), ctypes.sizeof(winapi.SHQUERYRBINFO)
        want = (56, 24) if is64 else (30, 20)
        return (a, b) == want, "SHFILEOPSTRUCTW=%d · SHQUERYRBINFO=%d (기대 %d·%d)" % (a, b, want[0], want[1])
    run("구조체 크기", t_struct)

    # 2. 드라이브 목록
    drives = []

    def t_drives():
        drives.extend(winapi.list_drives())
        if not drives:
            return False, "고정 드라이브를 하나도 못 찾았어요"
        sysd = (os.environ.get("SystemDrive") or "C:")[0].upper()
        for d in drives:
            print("     %s:  %-14s %-5s %-8s 전체 %-9s 남음 %-9s 일련번호 %s  %s%s%s%s" % (
                d["letter"], d["label"][:14], d["fs"], d["kind"], human(d["total"]), human(d["free"]), d["serial"],
                "외장 " if d.get("external") else "", "휴지통 있음 " if d.get("has_bin") else "휴지통 없음 ",
                "스캔 " if d.get("scan") else "", ("연결 " + (d.get("unc") or "")) if d.get("connected")
                else "연결 안 됨 " + (d.get("unc") or "")))
            for k in ("external", "has_bin", "scan", "connected", "unc"):
                if k not in d:
                    return False, "%s: 드라이브 정보에 %s 가 없어요" % (d["letter"], k)
            if d["kind"] not in winapi.KINDS:
                return False, "%s: 알 수 없는 종류 %r" % (d["letter"], d["kind"])
            if not d["connected"]:
                continue
            du = shutil.disk_usage(d["root"])
            if du.total != d["total"]:
                return False, "%s: 전체 용량이 shutil.disk_usage 와 달라요" % d["letter"]
        if sysd not in [d["letter"] for d in drives]:
            return False, "시스템 드라이브 %s: 가 목록에 없어요" % sysd
        return True, "%d개: %s" % (len(drives), ", ".join(d["letter"] + ":" for d in drives))
    run("드라이브 목록", t_drives)

    # 3. SSD/HDD 판별 · 외장 여부(BusType)
    for d in [x for x in drives if x["kind"] not in ("network",)]:
        def t_bus(d=d):
            sp, bus = winapi._win_device_info(d["letter"])
            names = {4: "1394", 7: "USB", 11: "SATA", 12: "SD", 13: "MMC", 17: "NVMe", 10: "SAS", 3: "ATA",
                     1: "SCSI", 8: "RAID", 9: "iSCSI", 18: "SCM", 19: "UFS"}
            if bus is None:
                return "WARN", "BusType 을 읽지 못했어요 → 외장=%s(판단 보류는 내장으로)" % d["external"]
            want = winapi.is_external_bus(bus)
            if d["kind"] == "usb":
                return True, "이동식(USB 메모리 등) · BusType %s" % names.get(bus, bus)
            return want == d["external"], "BusType %s → 외장=%s (탐색기·장치 관리자와 맞는지 확인해 주세요)" % (
                names.get(bus, bus), d["external"])
        run("외장 판별 %s:" % d["letter"], t_bus)
    for d in [x for x in drives if x["kind"] in ("ssd", "hdd", "unknown")]:
        def t_kind(d=d):
            raw = winapi._win_seek_penalty(d["letter"])
            how = "장치 질의" if raw is not None else "라벨 추정"
            if d["kind"] in ("ssd", "hdd"):
                return True, "%s (%s)" % (d["kind"].upper(), how)
            return "WARN", "판별하지 못했어요(장치 질의 실패, 라벨에 SSD/HDD 없음)"
        run("SSD/HDD %s:" % d["letter"], t_kind)

    # 3-2. 휴지통 유무(이동식은 $RECYCLE.BIN 이 이미 있을 때만, 네트워크는 없음)
    def t_has_bin():
        out, bad = [], []
        for d in drives:
            if not d["connected"]:
                continue
            exists = os.path.isdir(d["root"] + "$RECYCLE.BIN") if d["kind"] != "network" else False
            out.append("%s=%s" % (d["letter"], "있음" if d["has_bin"] else "없음"))
            if d["kind"] == "network" and d["has_bin"]:
                bad.append(d["letter"] + ": 네트워크인데 휴지통 있음")
            if d["kind"] == "usb" and d["has_bin"] and not exists:
                bad.append(d["letter"] + ": $RECYCLE.BIN 없는데 휴지통 있음")
        return (not bad), ", ".join(bad or out)
    run("휴지통 유무", t_has_bin)

    # 3-3. 매핑된 네트워크 드라이브의 UNC(WNetGetConnectionW)
    def t_wnet():
        nets = [d for d in drives if d["kind"] == "network" and len(d["letter"]) == 1]
        if not nets:
            return "SKIP", "매핑된 네트워크 드라이브가 없어요(탐색기에서 네트워크 드라이브 연결 후 다시)"
        bad = [d["letter"] for d in nets if not d.get("unc")]
        detail = ", ".join("%s:=%s(%s)" % (d["letter"], d.get("unc"), "연결" if d["connected"] else "끊김")
                           for d in nets)
        return (not bad), detail
    run("네트워크 드라이브 UNC", t_wnet)

    # 3-4. NAS 공유 목록(NetShareEnum) — --nas 를 줄 때만
    def t_shares():
        host = None
        if "--nas" in sys.argv:
            i = sys.argv.index("--nas")
            host = sys.argv[i + 1] if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--") else None
            if host is None:
                try:
                    host = input("     NAS 장치 이름이나 IP(예: ORICO, 192.168.0.10, 비우면 건너뛰기): ").strip()
                except EOFError:
                    host = ""
        if not host:
            return "SKIP", "--nas 장치이름 을 주면 공유 목록을 읽어 봐요"
        t0 = time.time()
        shares, err = winapi.list_network_shares(host)
        took = time.time() - t0
        if err:
            return False, "%s (%.1f초)" % (err, took)
        if not shares:
            return "WARN", "공유가 0개예요 (%.1f초)" % took
        info = winapi.location_info(shares[0]["unc"])
        return True, "%d개: %s (%.1f초) · %s → %s, 전체 %s 남음 %s" % (
            len(shares), ", ".join(x["name"] for x in shares[:8]), took, shares[0]["unc"],
            "연결" if info and info["connected"] else "연결 안 됨",
            human(info["total"]) if info else "-", human(info["free"]) if info else "-")
    run("NAS 공유 목록", t_shares)

    # 3-5. 죽은 NAS 가 앱을 막지 않는가(없는 장치 → 시간 제한 안에 연결 안 됨)
    def t_dead_nas():
        t0 = time.time()
        info = winapi.location_info("\\\\%s\\x" % ("db-nohost-" + RUN_ID))
        took = time.time() - t0
        ok = info is not None and not info["connected"] and took < winapi.NET_TIMEOUT + 1.5
        return ok, "%.1f초 만에 연결 안 됨으로 판정" % took
    run("없는 NAS 시간 제한", t_dead_nas)

    # 4. 휴지통 크기
    for d in [x for x in drives if x["connected"] and x["has_bin"]]:
        def t_bin(d=d):
            b, c = winapi.recycle_bin_info(d["root"])
            ok = isinstance(b, int) and isinstance(c, int) and b >= 0 and c >= 0
            return ok, "%s · %d개" % (human(b), c)
        run("휴지통 크기 %s:" % d["letter"], t_bin)

    # 5. 보호 경로
    temp = os.path.realpath(tempfile.gettempdir())       # 8.3 짧은 이름을 긴 이름으로
    tl = os.path.splitdrive(temp)[0][:1].upper()

    def t_safety():
        sysd = (os.environ.get("SystemDrive") or "C:")[:2]
        prof = os.path.realpath(os.environ.get("USERPROFILE", ""))
        cases = [(sysd + "\\", False), (sysd + "\\Windows", False), (sysd + "\\Program Files", False),
                 (os.path.join(prof, "Desktop"), False), (os.path.join(prof, "AppData"), False),
                 (os.path.join(temp, TAG + ".txt"), True), (os.path.join(prof, "Desktop", TAG), True)]
        bad = [(p, safety.check(p, drives)) for p, want in cases if safety.check(p, drives)[0] != want]
        if bad:
            return False, "; ".join("%s → %s" % (p, r) for p, r in bad)
        return True, "%d개 경로 판정 일치" % len(cases)
    run("보호 경로", t_safety)

    # 6. 휴지통으로 보내기 (파일·폴더)
    tdrive = next((d for d in drives if d["letter"] == tl and d["connected"]), None)

    def t_recycle():
        if tdrive is None:
            return "SKIP", "TEMP(%s) 드라이브가 고정 드라이브 목록에 없어요" % temp
        f = os.path.join(temp, TAG + ".txt")
        with open(f, "wb") as fh:
            fh.write(b"DriveBoard selftest\r\n" * 50)
        folder = os.path.join(temp, TAG + "-folder")
        created.extend([f, folder])               # 보내지 못하면 정리 단계에서 지운다
        mkfile(os.path.join(folder, "a.bin"), 2048)
        mkfile(os.path.join(folder, "sub", "b.bin"), 4096)
        b0, c0 = winapi.recycle_bin_info(tdrive["root"])
        ok1, e1 = winapi.recycle(f)
        ok2, e2 = winapi.recycle(folder)
        time.sleep(0.5)
        b1, c1 = winapi.recycle_bin_info(tdrive["root"])
        if not (ok1 and ok2):
            return False, "실패: %s / %s" % (e1, e2)
        if os.path.lexists(f) or os.path.lexists(folder):
            return False, "보냈다는데 파일이 남아 있어요"
        if c1 < c0 + 2:
            return False, ("휴지통 개수가 늘지 않았어요(%d→%d). 휴지통 설정이 '바로 삭제'일 수 있어요" % (c0, c1))
        return True, "휴지통 %d→%d개 (휴지통에 %s* 가 남아 있어요. 비워도 괜찮아요)" % (c0, c1, TAG)
    run("휴지통으로 보내기", t_recycle)

    # 7. 묶음 + 사용 중인 파일(개별 재시도 경로)
    def t_batch():
        if tdrive is None:
            return "SKIP", "TEMP 드라이브 없음"
        print("     (Windows 가 '사용 중인 파일' 창을 띄우면 [건너뛰기]를 눌러 주세요)", flush=True)
        paths = [os.path.join(temp, "%s-batch%d.txt" % (TAG, i)) for i in range(3)]
        created.extend(paths)
        for p in paths:
            with open(p, "wb") as fh:
                fh.write(b"x" * 100)
        missing = os.path.join(temp, TAG + "-missing.txt")
        held = open(paths[1], "rb")              # 파이썬 open 은 FILE_SHARE_DELETE 가 없어 옮길 수 없다
        try:
            res = winapi.recycle_many([paths[0], paths[1], missing, paths[2]])
        finally:
            held.close()
        got = [r[1] for r in res]
        detail = " / ".join("%s=%s" % (os.path.basename(r[0])[-12:], "OK" if r[1] else r[2]) for r in res)
        if os.path.lexists(paths[1]):
            os.remove(paths[1])                  # 우리가 만든 파일
        if got == [True, False, False, True] and [r[0] for r in res] == [paths[0], paths[1], missing, paths[2]]:
            return True, detail
        return False, detail
    run("묶음 휴지통(사용 중 파일 포함)", t_batch)

    # 8. 이동 왕복
    def find_other():
        cands = sorted(drives, key=lambda d: (d["kind"] == "network", bool(d["external"]) or d["kind"] == "usb"))
        for d in cands:
            if (d["letter"] == tl or not d["connected"] or d["free"] < 400 * (1 << 20)
                    or d["kind"] == "network" or not d["has_bin"]):
                continue
            p = os.path.join(d["root"], TAG)
            try:
                os.mkdir(p)
            except OSError:
                continue
            created.append(p)
            return d, p
        return None, None

    def t_move():
        if tdrive is None:
            return "SKIP", "TEMP 드라이브 없음"
        other, dest_dir = find_other()
        if other is None:
            return "SKIP", "옮겨 볼 다른 고정 드라이브(여유 400MB 이상, 폴더 생성 가능)가 없어요"
        src_parent = os.path.join(temp, TAG + "-move")
        os.mkdir(src_parent)
        created.append(src_parent)
        src = os.path.join(src_parent, "proj")
        mkfile(os.path.join(src, "a.aep"), 1 << 20)
        mkfile(os.path.join(src, "footage", "C0001.MP4"), 3 * (1 << 20) + 7, mtime=OLD + 60)
        mkfile(os.path.join(src, "footage", "zero.bin"), 0)
        mkdirs(os.path.join(src, "empty"))
        deep = os.path.join(src, *(["deep_" + "x" * 45] * 5), "long_name_file.bin")
        mkfile(deep, 1234)
        before = snapshot(src)
        drv = winapi.list_drives
        q = ops.JobQueue(data_home, drv, lambda p: safety.check(p, drv()))
        q.start()
        try:
            j = wait(q, q.submit_move([src], other["letter"], dest_dir=dest_dir, verify="full"))
            dst = os.path.join(dest_dir, "proj")
            if j["status"] != "done":
                return False, "이동 %s: %s %s" % (j["status"], j["message"], j["results"])
            if os.path.lexists(xp(src)):
                return False, "이동 뒤 원본이 남아 있어요"
            after = snapshot(dst)
            if after != before:
                return False, "옮긴 내용이 원본과 달라요(크기·수정 시각·해시 비교)"
            u = wait(q, q.undo(j["id"]))
            if u["status"] != "done":
                return False, "되돌리기 %s: %s" % (u["status"], u["message"])
            if snapshot(src) != before:
                return False, "되돌린 내용이 처음과 달라요"
            if os.path.lexists(xp(dst)):
                return False, "되돌린 뒤 대상에 사본이 남아 있어요"
            return True, "%s → %s: → 되돌리기, 파일 %d개·긴 경로(%d자) 포함 일치" % (
                tl, other["letter"], sum(1 for v in before.values() if v[0] == "f"), len(deep))
        finally:
            q.stop(10)
    run("이동 → 되돌리기", t_move)

    # 9. 이동 중 잠긴 파일 → 부분 완료, 원본 보존
    def t_locked():
        other = next((d for d in drives if os.path.join(d["root"], TAG) in created), None)
        if tdrive is None or other is None:
            return "SKIP", "이동 검사를 건너뛰어 함께 건너뛰었어요"
        dest_dir = os.path.join(other["root"], TAG)
        src = os.path.join(temp, TAG + "-move", "locked")
        mkfile(os.path.join(src, "free.bin"), 5000)
        locked = mkfile(os.path.join(src, "held.bin"), 6000)
        drv = winapi.list_drives
        q = ops.JobQueue(data_home, drv, lambda p: safety.check(p, drv()))
        q.start()
        held = open(locked, "rb")
        try:
            j = wait(q, q.submit_move([src], other["letter"], dest_dir=dest_dir, verify="quick"))
        finally:
            held.close()
            q.stop(10)
        ok = (j["status"] == "partial" and os.path.exists(locked)
              and not os.path.exists(os.path.join(src, "free.bin"))
              and os.path.exists(os.path.join(dest_dir, "locked", "free.bin"))
              and not os.path.exists(os.path.join(dest_dir, "locked", "held.bin")))
        return ok, "%s · %s" % (j["status"], (j["results"] or [{}])[0].get("error", ""))
    run("잠긴 파일 → 부분 완료", t_locked)

    # 9-2. 복사 왕복: 다른 드라이브로 복사 → 원본 그대로 → 되돌리기(복사본만 휴지통으로)
    def t_copy():
        other = next((d for d in drives if os.path.join(d["root"], TAG) in created), None)
        if tdrive is None or other is None:
            return "SKIP", "이동 검사를 건너뛰어 함께 건너뛰었어요"
        dest_dir = os.path.join(other["root"], TAG)
        src = os.path.join(temp, TAG + "-move", "copyme")
        mkfile(os.path.join(src, "a.aep"), 300000)
        mkfile(os.path.join(src, "sub", "b.mov"), 2 * (1 << 20) + 9, mtime=OLD + 5)
        before = snapshot(src)
        drv = winapi.list_drives
        q = ops.JobQueue(data_home, drv, lambda p: safety.check(p, drv()))
        q.start()
        try:
            j = wait(q, q.submit_move([src], None, dest_dir=dest_dir, verify="full", mode="copy"))
            dst = os.path.join(dest_dir, "copyme")
            if j["status"] != "done":
                return False, "복사 %s: %s %s" % (j["status"], j["message"], j["results"])
            if snapshot(src) != before or snapshot(dst) != before:
                return False, "원본이 바뀌었거나 복사본이 원본과 달라요"
            # 같은 드라이브 안 복사는 폴더를 골라야 한다
            try:
                q.submit_move([src], tl, mode="copy")
                return False, "같은 드라이브 복사가 폴더 없이 시작됐어요"
            except ops.OpError as e:
                if e.code != "need_dest":
                    return False, "같은 드라이브 복사 거부 코드가 %s 예요" % e.code
            u = wait(q, q.undo(j["id"]))
            if u["status"] != "done":
                return False, "복사 되돌리기 %s: %s" % (u["status"], u["message"])
            if os.path.lexists(xp(dst)):
                return False, "되돌린 뒤 복사본이 남아 있어요"
            if snapshot(src) != before:
                return False, "되돌린 뒤 원본이 달라졌어요"
            return True, "%s → %s: 복사 → 되돌리기(복사본은 %s: 휴지통에 있어요), 원본 그대로" % (
                tl, other["letter"], other["letter"])
        finally:
            q.stop(10)
    run("복사 → 되돌리기", t_copy)

    # 10. 그 밖
    def t_adobe():
        procs = winapi.running_adobe()
        return True, ("실행 중: " + ", ".join(procs)) if procs else "실행 중인 Adobe 앱 없음"
    run("Adobe 프로세스 확인", t_adobe)

    def t_long():
        p = "C:\\" + "\\".join(["d%03d" % i for i in range(70)])
        lp = winapi.long_path(p)
        return lp.startswith("\\\\?\\C:\\"), "%d자 → 접두사 %s" % (len(p), "있음" if lp != p else "없음")
    run("긴 경로 접두사", t_long)

    if "--reveal" in sys.argv:
        def t_reveal():
            return winapi.reveal(temp), "탐색기 창이 TEMP 폴더를 열었는지 눈으로 확인해 주세요"
        run("탐색기에서 보기", t_reveal)

    # 정리 — 우리가 만든 폴더만
    left = []
    for p in reversed(created):
        try:
            if not remove_ours(p):
                left.append(p)
        except OSError as e:
            left.append("%s (%s)" % (p, e))
    add("정리", "PASS" if not left else "FAIL", "남은 것 없음" if not left else "남은 것: " + ", ".join(left))

    # 표
    w1 = max(width(r[0]) for r in rows) + 2
    print("\n" + pad("항목", w1) + "결과   내용")
    print("-" * (w1 + 60))
    for name, res, detail in rows:
        print(pad(name, w1) + pad(res, 7) + detail)
    fails = [r for r in rows if r[1] == "FAIL"]
    print("\n%s — PASS %d · FAIL %d · 그 밖 %d" % (
        "모두 통과했어요" if not fails else "실패한 항목이 있어요",
        sum(1 for r in rows if r[1] == "PASS"), len(fails), sum(1 for r in rows if r[1] not in ("PASS", "FAIL"))))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
