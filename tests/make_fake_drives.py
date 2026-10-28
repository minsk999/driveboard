#!/usr/bin/env python3
"""Build fake drives for DriveBoard (SPEC §12, SPEC-2 A·B).

    python tests/make_fake_drives.py <out_dir> [--scale small|demo] [--profile lead|member|classic]
                                     [--seed N] [--clean]

Each sub-directory C, D, … of <out_dir> is one drive; a sub-directory named "N-xxxxxx" is a
registered network location (NAS). Files are SPARSE (open + truncate), so terabytes are
simulated while the real disk usage stays tiny. `<drive>/_drive.json` =
{"label","kind","total","system_bytes","external","has_bin","network","unc"};
a fake backend computes  free = total - real file sizes - .fakebin sizes - system_bytes.

Profiles: lead = the team lead's PC (7 internal drives + J external SSD + K USB stick + NAS),
member = a team member's PC (C SSD 931GB + D HDD 1.81TB), classic = the 7 internal drives only
(v1 layout). Default: lead for --scale demo, classic for --scale small (keeps v1 tests stable).

Team projects follow the naming rule (날짜_광고주_캠페인_작업내용_작업자 …) and most carry a small
REAL video in their render folder (generated once with ffmpeg into a cache dir; sparse
placeholders when ffmpeg is missing) so previews can play.

⚠ Sparse files are NOT for move tests: copying them writes real zeros. Use small files there.
"""

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

KiB = 1 << 10
MiB = 1 << 20
GiB = 1 << 30
TiB = 1 << 40
DAY = 86400.0

# letter, label, kind, total bytes, free bytes (screenshot: 757GB, 480GB, 406GB, 403GB, 246GB,
# 3.05TB, 3.04TB — Explorer truncates, so values sit just above those numbers)
DRIVES = [
    ("C", "로컬 디스크", "ssd", 4000787030016, int(757.4 * GiB)),
    ("D", "SSD1", "ssd", 4000787030016, int(480.4 * GiB)),
    ("E", "SSD2", "ssd", 2000398934016, int(406.3 * GiB)),
    ("F", "SSD3", "ssd", 2000398934016, int(403.5 * GiB)),
    ("G", "SSD4", "ssd", 1000202273280, int(246.4 * GiB)),
    ("H", "HDD1", "hdd", 8001563222016, int(3.054 * TiB)),
    ("I", "HDD2", "hdd", 8001563222016, int(3.044 * TiB)),
]
# share of "used" that is real files; the rest is system_bytes (MFT, hidden, unreadable …)
FILE_RATIO = {"C": 0.90, "D": 0.965, "E": 0.965, "F": 0.965, "G": 0.95, "H": 0.975,
              "I": 0.975}
SCALES = {"small": 0.08, "demo": 1.0}
PROFILES = ("lead", "member", "classic")

# extra locations of the lead profile: key, label, kind, total, free, meta
NAS_UNC = "\\\\ORICO\\영상백업"


def network_key(unc):
    """same formula as core.winapi.network_key: 'N-' + sha1(lowercased normalized UNC)[:6]."""
    u = "\\\\" + "\\".join(c for c in unc.replace("/", "\\")[2:].split("\\") if c)
    return "N-" + hashlib.sha1(u.lower().encode("utf-8")).hexdigest()[:6]


EXTRA_DRIVES = [
    ("J", "Samsung T7", "ssd", 2000398934016, int(742.6 * GiB),
     {"external": True, "has_bin": True}),
    ("K", "USB 메모리", "usb", 64023257088, int(21.3 * GiB),
     {"external": True, "has_bin": False}),
    (network_key(NAS_UNC), "ORICO NAS · 영상백업", "network", 8001563222016, int(2.61 * TiB),
     {"external": False, "has_bin": False, "network": True, "unc": NAS_UNC}),
]
MEMBER_DRIVES = [
    ("C", "로컬 디스크", "ssd", 1000202273280, int(212.6 * GiB), {}),
    ("D", "HDD", "hdd", 2000398934016, int(640.3 * GiB), {}),
]
EXTRA_RATIO = {"J": 0.97, "K": 0.95}
NET_RATIO = 0.975
MEMBER_RATIO = {"C": 0.90, "D": 0.97}
# share of each internal drive's file budget that goes to team-named projects (lead profile)
TEAM_RESERVE = {"C": 0.04, "D": 0.12, "E": 0.10, "F": 0.10, "H": 0.08, "I": 0.04}
USER = "홍길동"
PC = "STUDIO-EDIT01"


class Plan:
    """Files to create on one drive: rel path (forward slashes) -> (size, mtime)."""

    def __init__(self, letter, rng, now, S):
        self.letter = letter
        self.rng = rng
        self.now = now
        self.S = S
        self.files = {}
        self.dirs = set()
        self.bin = []          # (name, size, mtime) for .fakebin
        self.real = {}         # rel -> (source file to copy, mtime) — real (non-sparse) files

    def add(self, rel, size, mtime):
        if rel in self.files or rel in self.real:
            raise ValueError("duplicate fake path: %s:%s" % (self.letter, rel))
        self.files[rel] = (max(0, int(size)), min(float(mtime), self.now - 60))

    def add_real(self, rel, src, mtime):
        if rel in self.files or rel in self.real:
            raise ValueError("duplicate fake path: %s:%s" % (self.letter, rel))
        self.real[rel] = (src, min(float(mtime), self.now - 60))

    def mkdir(self, rel):
        self.dirs.add(rel)

    def n(self, base, lo=1):
        return max(lo, int(round(base * self.S)))

    def ago(self, days, spread=0.0):
        return self.now - (days + self.rng.uniform(0, spread)) * DAY

    def total(self):
        return sum(s for s, _m in self.files.values())


def alloc(plan, target, entries):
    """entries [(rel, weight, mtime)] -> sizes proportional to weight, summing to target."""
    if not entries:
        return
    sw = float(sum(w for _r, w, _m in entries)) or 1.0
    acc = 0
    last = len(entries) - 1
    for i, (rel, w, mt) in enumerate(entries):
        sz = int(target - acc) if i == last else int(target * w / sw)
        acc += sz
        plan.add(rel, sz, mt)


def jit(rng, a=0.6, b=1.4):
    return rng.uniform(a, b)


# ---------------------------------------------------------------- content blocks

CAM_AVG = {"sony": 4.5 * GiB, "arri": 28 * GiB, "red": 3.9 * GiB, "gopro": 3.7 * GiB,
           "dji": 2.2 * GiB, "iphone": 1.6 * GiB}


def footage(plan, base, target, age, cam, max_clips):
    """Camera originals under base. Returns [(rel, stem)] of main clips."""
    r = plan.rng
    out = []
    ent = []
    n = int(max(3, min(max_clips, target / CAM_AVG[cam])))
    ncards = 1 + (n > 12) + (n > 40)
    per = max(1, n // ncards)
    mt0 = age + 14
    k = 0
    for card in range(ncards):
        cn = per if card < ncards - 1 else n - per * (ncards - 1)
        roll = "%s001" % "ABC"[card]
        for j in range(cn):
            k += 1
            mt = plan.ago(mt0 - card, 0.3)
            if cam == "sony":
                stem = "C%04d" % k
                rel = "%s/%s/%s.MP4" % (base, roll, stem)
                plan.add("%s/%s/%sM01.XML" % (base, roll, stem), int(5.8 * KiB), mt)
            elif cam == "arri":
                stem = "%sC%03d_%s_R%s" % (roll, j + 1, time.strftime("%y%m%d", time.localtime(mt)),
                                           "1AB")
                rel = "%s/%sR1AB/%s.mxf" % (base, roll, stem)
            elif cam == "red":
                stem = "%s_C%03d_0312XY" % (roll, j + 1)
                segs = r.randint(1, 4)
                for s in range(1, segs + 1):
                    ent.append(("%s/%s_0312XY.RDM/%s.RDC/%s_%03d.R3D" % (base, roll, stem, stem, s),
                                jit(r), mt))
                out.append(("%s/%s_0312XY.RDM/%s.RDC/%s_001.R3D" % (base, roll, stem, stem), stem))
                continue
            elif cam == "gopro":
                stem = "GX%02d%04d" % (1, 40 + k)
                rel = "%s/%s/100GOPRO/%s.MP4" % (base, roll, stem)
                plan.add("%s/%s/100GOPRO/%s.THM" % (base, roll, stem), int(14 * KiB), mt)
                ent.append(("%s/%s/100GOPRO/%s.LRV" % (base, roll, stem), 0.05, mt))
            elif cam == "dji":
                stem = "DJI_%04d" % (k + 100)
                rel = "%s/%s/%s.MP4" % (base, roll, stem)
                plan.add("%s/%s/%s.SRT" % (base, roll, stem), int(31 * KiB), mt)
            else:
                stem = "IMG_%04d" % (2000 + k)
                rel = "%s/%s/%s.MOV" % (base, roll, stem)
            ent.append((rel, jit(r, 0.3, 1.8), mt))
            out.append((rel, stem))
    alloc(plan, target, ent)
    return out


def exr_sequence(plan, base, shot, frames, target, mt, ext="exr", digits=4):
    r = plan.rng
    ent = [("%s/%s_%0*d.%s" % (base, shot, digits, i, ext), jit(r, 0.9, 1.1),
            mt - (frames - i) * 2.0) for i in range(1, frames + 1)]
    alloc(plan, target, ent)


def project(plan, root, name, short, target, age, cam="sony", nseq=2, frames=(100, 1500),
            premiere=False):
    """A typical video-team project folder."""
    r = plan.rng
    if plan.S < 1:
        frames = (20, 40)
    base = root + "/" + name if root else name
    sh = {"footage": .54, "proxy": .05, "render": .10, "deliver": .035, "exr": .18,
          "audio": .02, "image": .03, "aep": .012, "autosave": .006, "misc": .002, "cache": .025}
    tot = sum(sh.values())
    T = {k: int(target * v / tot) for k, v in sh.items()}
    fdir = base + "/" + r.choice(["footage", "01_footage", "촬영본", "원본"])
    clips = footage(plan, fdir, T["footage"], age, cam, max_clips=plan.n(140, 4))
    # proxies for a subset of clips
    pent = []
    for rel, stem in clips[: max(2, int(len(clips) * 0.7))]:
        pent.append(("%s/Proxy/%s_Proxy.mp4" % (base, stem), jit(r), plan.ago(age + 12, 1)))
    alloc(plan, T["proxy"], pent)
    # renders
    rdir = base + "/" + r.choice(["render", "06_render", "렌더", "output"])
    rent = []
    nver = plan.n(8, 3)
    for v in range(1, nver + 1):
        rent.append(("%s/%s_comp_v%02d.mp4" % (rdir, short, v), jit(r), plan.ago(age + nver - v)))
    rent.append(("%s/%s_final_v%02d.mov" % (rdir, short, nver), 6.0, plan.ago(age)))
    rent.append(("%s/%s_preview.mp4" % (rdir, short), 0.5, plan.ago(age + 2)))
    alloc(plan, T["render"], rent)
    dent = [("%s/납품/%s_%ds_최종.mp4" % (rdir, short, s), jit(r), plan.ago(age, 1))
            for s in (6, 15, 30)]
    dent.append(("%s/납품/%s_30s_최종_자막.mov" % (rdir, short), 3.0, plan.ago(age)))
    alloc(plan, T["deliver"], dent)
    # image sequences
    for i in range(nseq):
        nf = r.randint(*frames)
        shot = "shot%03d" % ((i + 1) * 10)
        exr_sequence(plan, "%s/exr/%s" % (rdir, shot), shot, nf, T["exr"] // nseq,
                     plan.ago(age + 3 + i))
    # project / audio / images / docs
    aent = [("%s/aep/%s_v%02d.aep" % (base, short, v), jit(r), plan.ago(age + 10 - v))
            for v in range(1, plan.n(10, 3) + 1)]
    if premiere:
        aent.append(("%s/aep/%s_편집.prproj" % (base, short), 2.0, plan.ago(age + 1)))
    alloc(plan, T["aep"], aent)
    asdir = ("%s/aep/Adobe Premiere Pro Auto-Save" if premiere
             else "%s/aep/Adobe After Effects Auto-Save") % base
    alloc(plan, T["autosave"], [("%s/%s_v03 auto-save %d.%s" % (asdir, short, k,
                                                                 "prproj" if premiere else "aep"),
                                 1.0, plan.ago(age + k)) for k in range(1, plan.n(12, 3) + 1)])
    alloc(plan, T["audio"], [("%s/audio/%s" % (base, nm), w, plan.ago(age + 6))
                             for nm, w in (("BGM_v2.wav", 3), ("NAR_final.wav", 2),
                                           ("SFX_whoosh_01.wav", .3), ("SFX_hit_02.wav", .3),
                                           ("가이드녹음.m4a", .2))])
    ient = [("%s/image/키비주얼_v3.psd" % base, 8, plan.ago(age + 8)),
            ("%s/image/로고.ai" % base, .5, plan.ago(age + 30)),
            ("%s/image/배경_합성.psb" % base, 6, plan.ago(age + 7))]
    for k in range(1, plan.n(30, 6) + 1):
        ient.append(("%s/image/ref/ref_%03d.jpg" % (base, k), .05, plan.ago(age + 20)))
    alloc(plan, T["image"], ient)
    alloc(plan, T["misc"], [("%s/doc/%s" % (base, nm), w, plan.ago(age + 25))
                            for nm, w in (("기획안_v3.pdf", 3), ("콘티.pptx", 5),
                                          ("촬영스케줄.xlsx", .2), ("스크립트_최종.docx", .3),
                                          ("메모.txt", .01))])
    # project-local media cache (Premiere "Media Cache Files" inside the project)
    media_cache(plan, base + "/Media Cache Files", T["cache"], age, n=plan.n(40, 5))
    plan.mkdir(base + "/99_temp")       # an empty folder


def media_cache(plan, base, target, age, n=None):
    r = plan.rng
    n = n or plan.n(600, 20)
    ent = []
    for k in range(n):
        stem = "C%04d %d" % (k + 1, 48000)
        mt = plan.ago(age, 40)
        ent.append(("%s/%s.cfa" % (base, stem), jit(r), mt))
        if k % 3 == 0:
            plan.add("%s/%s.pek" % (base, stem), int(r.uniform(80, 400) * KiB), mt)
    alloc(plan, target, ent)
    parent = base.rsplit("/", 1)[0] + "/" if "/" in base else ""
    db = parent + "Media Cache/Media Cache Database.mcdb"
    if db not in plan.files:
        plan.add(db, int(42 * MiB), plan.ago(age))


def ae_disk_cache(plan, base, target, age):
    r = plan.rng
    n = plan.n(1500, 30)
    ent = []
    for k in range(n):
        ent.append(("%s/Disk Cache - %s/%02X/%08x.aecache" % (base, PC, k % 64, r.getrandbits(32)),
                    jit(r), plan.ago(age, 30)))
    alloc(plan, target, ent)


def temp_block(plan, base, target):
    r = plan.rng
    ent = []
    for k in range(plan.n(400, 15)):
        sub = r.choice(["", "AdobeLogs/", "{%08X-1A2B}/" % r.getrandbits(32), "AME/"])
        nm = r.choice(["~DF%04X.tmp" % r.getrandbits(16), "render_%d.tmp" % k,
                       "AEtemp_%d.mov" % k, "crash_%d.dmp" % k, "log_%d.txt" % k])
        ent.append(("%s/%s%s" % (base, sub, nm), jit(r, 0.1, 3), plan.ago(r.uniform(0, 60))))
    uniq = {}
    for e in ent:
        uniq.setdefault(e[0], e)
    alloc(plan, target, list(uniq.values()))


def windows_block(plan, target):
    r = plan.rng
    ent = []
    for k in range(plan.n(1800, 40)):
        d = r.choice(["Windows/System32", "Windows/SysWOW64", "Windows/WinSxS/amd64_microsoft-windows",
                      "Windows/System32/DriverStore/FileRepository", "Windows/Fonts"])
        ext = "ttf" if d.endswith("Fonts") else r.choice(["dll", "dll", "dll", "sys", "exe", "mui"])
        ent.append(("%s/f%05d.%s" % (d, k, ext), jit(r, 0.05, 3), plan.ago(r.uniform(30, 400))))
    ent.append(("Windows/Temp/MpCmdRun.log", 0.01, plan.ago(1)))
    ent.append(("Windows/Installer/1a2b3c.msi", 4, plan.ago(200)))
    alloc(plan, target, ent)
    plan.add("pagefile.sys", 32 * GiB, plan.ago(0.01))
    plan.add("hiberfil.sys", int(25.4 * GiB), plan.ago(0.5))
    plan.add("swapfile.sys", 256 * MiB, plan.ago(0.01))
    plan.add("DumpStack.log.tmp", 8 * KiB, plan.ago(0.01))
    plan.add("System Volume Information/tracking.log", 20 * KiB, plan.ago(2))
    plan.add("$WinREAgent/Scratch/a.dat", 12 * MiB, plan.ago(90))


def program_files_block(plan, target):
    r = plan.rng
    apps = [("Program Files/Adobe/Adobe After Effects 2025/Support Files", "AfterFX.exe", 30),
            ("Program Files/Adobe/Adobe Premiere Pro 2025", "Adobe Premiere Pro.exe", 25),
            ("Program Files/Adobe/Adobe Media Encoder 2025", "Adobe Media Encoder.exe", 12),
            ("Program Files/Blackmagic Design/DaVinci Resolve", "Resolve.exe", 14),
            ("Program Files/Maxon Cinema 4D 2025", "Cinema 4D.exe", 10),
            ("Program Files (x86)/Common Files/Adobe", "AdobeGenuineHelper.exe", 3),
            ("ProgramData/Adobe/CameraRaw", "", 3),
            ("ProgramData/Microsoft/Windows Defender/Definition Updates", "", 2)]
    ent = []
    for base, exe, w in apps:
        if exe:
            ent.append(("%s/%s" % (base, exe), w * 0.1, plan.ago(60)))
        for k in range(plan.n(max(10, w * 12), 3)):
            ext = r.choice(["dll", "dll", "aex", "dat", "pak", "xml"])
            sub = "Plug-ins/" if ext == "aex" else ""
            ent.append(("%s/%s%s_%04d.%s" % (base, sub, "lib", k, ext), jit(r, 0.05, 2) * w / 30,
                        plan.ago(r.uniform(40, 200))))
    alloc(plan, target, ent)


def user_profile(plan, target, age_mix):
    """C:\\Users\\<USER>: AppData (app + cache), Downloads, Desktop, Pictures, Videos."""
    r = plan.rng
    U = "Users/" + USER
    sh = {"mediacache": .38, "temp": .05, "appdata_app": .02, "downloads": .12, "desktop": .18,
          "documents": .05, "pictures": .03, "videos": .17}
    tot = sum(sh.values())
    T = {k: int(target * v / tot) for k, v in sh.items()}
    media_cache(plan, U + "/AppData/Roaming/Adobe/Common/Media Cache Files", T["mediacache"], 20)
    temp_block(plan, U + "/AppData/Local/Temp", T["temp"])
    alloc(plan, T["appdata_app"],
          [("%s/AppData/Local/Adobe/After Effects/25.0/%s" % (U, nm), w, plan.ago(3))
           for nm, w in (("Adobe After Effects 25.0 Prefs.txt", .01), ("FontCache.dat", 1),
                         ("Effect Presets.db", .4))] +
          [("%s/AppData/Local/Google/Chrome/User Data/Default/Cache/Cache_Data/f_%06x" % (U, k),
            .05, plan.ago(r.uniform(0, 30))) for k in range(plan.n(120, 5))])
    dl = [("%s/Downloads/%s" % (U, nm), w, plan.ago(d)) for nm, w, d in (
        ("AfterEffects_25_0_Installer.exe", 3, 80), ("소재_모음_2025.zip", 20, 30),
        ("레퍼런스영상_final.mp4", 4, 12), ("폰트_Pretendard.zip", .1, 200),
        ("PremierePro_Installer.exe", 3, 120), ("고객사_전달자료(2).zip", 12, 45),
        ("Windows11_23H2.iso", 6, 300), ("견적서.pdf", .01, 20))]
    alloc(plan, T["downloads"], dl)
    project(plan, U + "/Desktop", "임시작업_자막수정", "자막", T["desktop"], age_mix[0], cam="iphone",
            nseq=1, frames=(100, 300))
    alloc(plan, T["documents"],
          [("%s/Documents/Adobe/After Effects 2025/템플릿_%02d.aep" % (U, k), 1, plan.ago(100 + k))
           for k in range(plan.n(20, 4))] +
          [("%s/Documents/견적/2025_견적서_%02d.xlsx" % (U, k), .01, plan.ago(30 + k))
           for k in range(plan.n(15, 3))])
    pics = [("%s/Pictures/Screenshots/스크린샷 2025-%02d-%02d %02d%02d%02d.png" % (
        U, 1 + k % 12, 1 + k % 28, k % 24, k % 60, (k * 7) % 60), 1, plan.ago(r.uniform(0, 300)))
        for k in range(plan.n(150, 8))]
    alloc(plan, T["pictures"], pics)
    alloc(plan, T["videos"],
          [("%s/Videos/Captures/화면녹화_%03d.mp4" % (U, k), jit(r), plan.ago(r.uniform(5, 500)))
           for k in range(plan.n(25, 3))] +
          [("%s/Videos/교육영상_AE_기초.mp4" % U, 5, plan.ago(400))])
    plan.add("Users/Public/Desktop/desktop.ini", 174, plan.ago(700))


def archive_zips(plan, base, target, names, age):
    ent = [("%s/%s" % (base, nm), jit(plan.rng), plan.ago(age + i * 30)) for i, nm in enumerate(names)]
    alloc(plan, target, ent)


def dcim_block(plan, base, target, age):
    r = plan.rng
    ent = []
    for k in range(plan.n(400, 10)):
        ent.append(("%s/DCIM/100MSDCF/DSC%05d.JPG" % (base, 1000 + k), .006, plan.ago(age, 2)))
        if k % 4 == 0:
            ent.append(("%s/DCIM/100MSDCF/DSC%05d.ARW" % (base, 1000 + k), .025, plan.ago(age, 2)))
    for k in range(plan.n(30, 3)):
        ent.append(("%s/PRIVATE/M4ROOT/CLIP/C%04d.MP4" % (base, k + 1), jit(r, .5, 1.5),
                    plan.ago(age, 2)))
    alloc(plan, target, ent)


def copy_entries(src, dst, pairs):
    """Duplicate (src_rel -> dst_rel) keeping exact size and mtime. Returns bytes copied."""
    n = 0
    for s, d in pairs:
        size, mt = src.files[s]
        dst.add(d, size, mt)
        n += size
    return n


# ---------------------------------------------------------------- demo media (real, tiny)

DEMO_MEDIA_VERSION = "v1"
# (file name, lavfi source, seconds, codec)
DEMO_CLIPS = [
    ("demo_testsrc2.mp4", "testsrc2=size=640x360:rate=30", 3, "h264"),
    ("demo_mandelbrot.mp4", "mandelbrot=size=640x360:rate=25", 3, "h264"),
    ("demo_gradients.mp4", "gradients=size=640x360:rate=30:speed=0.04", 4, "h264"),
    ("demo_smpte.mp4", "smptehdbars=size=640x360:rate=30", 2, "h264"),
    ("demo_life.mp4", "life=size=160x90:rate=15:mold=10:ratio=0.2:death_color=#1a1d29"
                      ":life_color=#a2ff00,scale=640:360:flags=neighbor", 3, "h264"),
    ("demo_rgb.mp4", "rgbtestsrc=size=640x360:rate=30,hue=H=2*PI*t/3", 3, "h264"),
    ("demo_prores_a.mov", "testsrc2=size=640x360:rate=25", 2, "prores"),
    ("demo_prores_b.mov", "gradients=size=640x360:rate=25:speed=0.05", 2, "prores"),
]
_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
          "C:\\Windows\\Fonts\\arialbd.ttf", "/Library/Fonts/Arial Bold.ttf")


def _ffmpeg():
    return os.environ.get("DRIVEBOARD_FFMPEG") or shutil.which("ffmpeg")


def media_cache_dir():
    return os.environ.get("DRIVEBOARD_DEMO_MEDIA") or os.path.join(
        tempfile.gettempdir(), "driveboard-demo-media-" + DEMO_MEDIA_VERSION)


def _clip_cmd(ff, src, secs, codec, label, out):
    font = next((f for f in _FONTS if os.path.isfile(f)), None)
    vf = []
    if font:
        ff_font = font.replace("\\", "/").replace(":", "\\:")
        vf.append("drawtext=fontfile='%s':text='%s':x=24:y=h-th-24:fontsize=28:fontcolor=white"
                  ":box=1:boxcolor=black@0.45:boxborderw=8" % (ff_font, label))
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(secs)]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    if codec == "prores":
        cmd += ["-c:v", "prores_ks", "-profile:v", "0", "-pix_fmt", "yuv422p10le",
                "-c:a", "pcm_s16le"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart"]
    cmd += ["-shortest", out]
    return cmd


def demo_clips(cache_dir=None):
    """{'h264': [paths], 'prores': [paths]} of tiny real clips (made once, reused).

    Empty lists when ffmpeg is missing or fails — callers then write sparse placeholders.
    """
    out = {"h264": [], "prores": []}
    ff = _ffmpeg()
    cache_dir = cache_dir or media_cache_dir()
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        return out
    for i, (nm, src, secs, codec) in enumerate(DEMO_CLIPS):
        dst = os.path.join(cache_dir, nm)
        if not (os.path.isfile(dst) and os.path.getsize(dst) > 1000):
            if not ff:
                continue
            tmp = dst + ".%d.part%s" % (os.getpid(), os.path.splitext(nm)[1])
            try:
                r = subprocess.run(_clip_cmd(ff, src, secs, codec, "DriveBoard DEMO %d" % (i + 1),
                                             tmp), capture_output=True, timeout=120)
                if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) < 1000:
                    # drawtext may be missing from this ffmpeg build: try once without text
                    cmd = _clip_cmd(ff, src, secs, codec, "", tmp)
                    if "-vf" in cmd:
                        k = cmd.index("-vf")
                        del cmd[k:k + 2]
                    r = subprocess.run(cmd, capture_output=True, timeout=120)
                if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) >= 1000:
                    os.replace(tmp, dst)
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                if os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
        if os.path.isfile(dst) and os.path.getsize(dst) > 1000:
            out[codec].append(dst)
    return out


# ---------------------------------------------------------------- team projects (SPEC-2 B)

TEAM_CLIENTS = [
    ("소담코스", ["녹차", "비타C", "청귤"], "GD"), ("한결증권", ["브랜드필름", "유튜브", "ISA"], "KW"),
    ("바른기술", ["인터뷰", "채용", "IR"], "DW"), ("한빛은행", ["런칭", "연말", "앱개편"], "HB"),
    ("새솔제약", ["유튜브", "런칭"], "SS"), ("푸른항공", ["기내영상", "취항"], "PA"),
    ("다온전자", ["신제품", "전시"], "DO"), ("라온식품", ["TVC", "SNS"], "RF"),
    ("미래캐피탈", ["브랜드", "혜택"], "MC"), ("하늘건설", ["분양", "홍보관"], "HN"),
    ("별빛화장품", ["가을캠페인", "리뉴얼"], "BB"), ("오름카드", ["혜택", "이벤트"], "OR"),
]
# names written differently from the canonical client (client_aliases demo)
TEAM_ALIASES = {"한결증권": ["한결", "KIWOOM", "Kiwoom"], "한빛은행": ["한빛"], "바른기술": ["다우"]}
TEAM_TASKS = ["배너결합", "편집", "모션", "숏폼", "리사이즈", "자막", "컷편집", "색보정", "인터뷰",
              "진정크림_배너결합", "15s_편집", "썸네일"]
TEAM_WORKERS = ["KMS", "HYC", "JJH", "PNJ", "KBH", "GJH"]
TEAM_RES = ["1920x1080", "1080x1920", "1080x1080", "1080x1350"]
TEAM_KINDS = ["@MB", "@BC", "@SZ", "@ED", "@SH"]
# fixed examples named in SPEC-2 (placed on the lead's D / F / E and the member's D)
TEAM_FIXED = [("260803_소담코스_녹차_진정크림_배너결합_KMS", "소담코스", "GD"),
              ("251023_한결증권_브랜드필름_편집_HYC", "한결증권", "KW"),
              ("260115_바른기술_인터뷰_JJH", "바른기술", "DW"),
              ("소담코스_진정크림", "소담코스", "GD"),
              ("2025-11_한빛은행_런칭", "한빛은행", "HB")]


def _ymd(now, age):
    return time.localtime(now - age * DAY)


def team_name(rng, now, age, form=None):
    """(folder name, short code) in one of the forms the team actually uses."""
    client, camps, short = rng.choice(TEAM_CLIENTS)
    camp = rng.choice(camps)
    task = rng.choice(TEAM_TASKS)
    w = rng.choice(TEAM_WORKERS)
    t = _ymd(now, age)
    if form is None:
        form = rng.choices(["brc", "brc_full", "alias", "simple", "ym", "ymd8", "spaced"],
                           [52, 10, 8, 8, 8, 7, 7])[0]
    if form == "alias" and client in TEAM_ALIASES:
        client = rng.choice(TEAM_ALIASES[client])
    elif form == "alias":
        form = "brc"
    d6 = time.strftime("%y%m%d", t)
    if form in ("brc", "alias"):
        name = "%s_%s_%s_%s_%s" % (d6, client, camp, task, w)
    elif form == "brc_full":
        name = "%s_%s_%s_%dp_%s_%s_%s_%s" % (d6, client, camp, rng.randint(1, 4), task,
                                            rng.choice(TEAM_RES), w, rng.choice(TEAM_KINDS))
    elif form == "simple":
        name = "%s_%s" % (client, camp)
    elif form == "ym":
        name = "%s_%s_%s" % (time.strftime("%Y-%m", t), client, camp)
    elif form == "ymd8":
        name = "%s_%s" % (time.strftime("%Y%m%d", t), client)
    else:
        name = "%s %s %s" % (time.strftime("%Y-%m-%d", t), client, camp)
    return name, short


def team_project(plan, base, name, short, target, age, clips, video="h264", rev=False,
                 aep=True):
    """A small team project: aep + footage + render (+ one REAL clip) + audio + image.

    video: 'h264' | 'prores' | 'none'. The real clip is the newest file of the render folder
    so it becomes the folder's representative video (SPEC-2 B.3).
    """
    r = plan.rng
    root = base + "/" + name if base else name
    ent = []
    if aep:
        for v in range(1, plan.n(4, 1) + 1):
            ent.append(("%s/aep/%s_v%02d.aep" % (root, short, v), 0.004, plan.ago(age - v * 0.5)))
    if video != "none":
        for k in range(1, plan.n(10, 2) + 1):
            ent.append(("%s/footage/C%04d.MP4" % (root, k), jit(r, 0.5, 1.5), plan.ago(age + 6)))
        rd = root + "/" + r.choice(["render", "06_render", "렌더", "output"])
        for v in range(1, plan.n(3, 1) + 1):
            ent.append(("%s/%s_comp_v%02d.mp4" % (rd, short, v), 0.08, plan.ago(age - 1 + v * 0.1)))
        ent.append(("%s/%s_master.mov" % (rd, short), 0.25, plan.ago(age - 1.5)))
        mt = plan.ago(age - 2)
        if video == "prores":
            rel, pool = "%s/%s_최종_ProRes.mov" % (rd, short), clips.get("prores") or []
        else:
            rel, pool = "%s/%s_15s_최종.mp4" % (rd, short), clips.get("h264") or []
        if pool:
            plan.add_real(rel, r.choice(pool), mt)
        else:
            plan.add(rel, int(r.uniform(120, 300) * KiB), mt)      # no ffmpeg: placeholder
        if rev:
            d6 = time.strftime("%y%m%d", _ymd(plan.now, max(0.0, age - 3)))
            ent.append(("%s/%s_수정본/%s_수정.mp4" % (root, d6, short), 0.05, plan.ago(age - 3)))
    ent.append(("%s/audio/BGM.wav" % root, 0.01, plan.ago(age + 2)))
    ent.append(("%s/image/%s_키비주얼.psd" % (root, short), 0.02, plan.ago(age + 1)))
    alloc(plan, target, ent)


def team_block(plan, base, n, target, age_range, clips, fixed=(), forms=None, rng=None):
    """n team projects (plus `fixed` [(name, client, short)]) under `base`, sharing `target`."""
    rng = rng or plan.rng
    specs = []
    used = set()
    for name, _client, short in fixed:
        pd = None
        for n_chars, fmt in ((6, "%y%m%d"), (7, "%Y-%m")):
            try:
                t = time.strptime(name[:n_chars], fmt)
            except ValueError:
                continue
            pd = max(3.0, (plan.now - time.mktime(t)) / DAY - 0.5)
            break
        specs.append((name, short, pd if pd is not None else rng.uniform(*age_range),
                      pd is not None))
        used.add(name)
    tries = 0
    while len(specs) < len(fixed) + n and tries < n * 20:
        tries += 1
        age = rng.uniform(*age_range)
        name, short = team_name(rng, plan.now, age, rng.choice(forms) if forms else None)
        if name in used:
            continue
        used.add(name)
        specs.append((name, short, age, True))
    if not specs:
        return []
    weights = [jit(rng, 0.3, 1.7) for _ in specs]
    sw = sum(weights)
    out = []
    for i, ((name, short, age, aep), w) in enumerate(zip(specs, weights)):
        video = "none" if i % 7 == 5 else ("prores" if i % 9 == 4 else "h264")
        # a fixed name without a date (소담코스_진정크림) gets no .aep: found only via project_roots
        team_project(plan, base, name, short, int(target * w / sw), age, clips, video=video,
                     rev=(aep and i % 5 == 1), aep=aep)
        out.append(name)
    return out


# ---------------------------------------------------------------- per-drive recipes

def _used(info, L, ratio):
    _lab, _kind, tot, free, _x = info[L]
    return int((tot - free) * ratio)


def build_plans(rng, now, S, profile="classic", clips=None):
    """(plans, info): plans {key: Plan}, info {key: (label, kind, total, free, extra_meta)}."""
    if profile == "member":
        return build_member_plans(rng, now, S, clips or {})
    P = {L: Plan(L, rng, now, S) for L, *_r in DRIVES}
    info = {L: (lab, kind, tot, free, {}) for L, lab, kind, tot, free in DRIVES}
    fr = (12, 20)
    team = profile == "lead"

    def budget(L, fixed=0):
        binb = sum(s for _n, s, _m in P[L].bin)
        keep = 1.0 - (TEAM_RESERVE.get(L, 0.0) if team else 0.0)
        return int(_used(info, L, FILE_RATIO[L]) * keep) - binb - fixed

    # recycle-bin contents (count toward used)
    P["C"].bin += [("20250911-142233_AfterEffects_24_Installer.exe", int(2.4 * GiB), now - 46 * DAY),
                   ("20251020-093010_화면녹화_임시.mp4", int(6.1 * GiB), now - 7 * DAY)]
    P["D"].bin += [("20251002-181500_BF_comp_v01.mov", int(38.5 * GiB), now - 25 * DAY),
                   ("20251015-110203_shot010_test.mp4", int(4.2 * GiB), now - 12 * DAY)]
    P["E"].bin += [("20250830-200000_카드백업_중복.zip", int(1.2 * GiB), now - 58 * DAY)]
    P["G"].bin += [("20251021-080000_Disk Cache 정리.aecache", int(15 * GiB), now - 6 * DAY)]
    P["I"].bin += [("20240612-120000_2022_백업_구버전.zip", int(120 * GiB), now - 500 * DAY)]

    # D: active projects
    d = P["D"]
    B = budget("D")
    projs = [("2025_한결증권_브랜드필름", "BF", .30, 18, "sony", 3),
             ("2025_한빛은행_디지털캠페인", "HB", .20, 45, "arri", 2),
             ("2025_새솔제약_유튜브시리즈", "SS", .16, 70, "sony", 2),
             ("2025_푸른항공_기내영상", "PA", .14, 30, "red", 2),
             ("2025_다온전자_신제품런칭", "DO", .12, 9, "gopro", 2)]
    for name, short, share, age, cam, nseq in projs:
        project(d, "", name, short, int(B * share), age, cam=cam, nseq=nseq, frames=(100, 1500) if S >= 1 else (20, 40))
    media_cache(d, "Media Cache Files", int(B * .05), 5)
    alloc(d, int(B * .03), [("_공용소스/효과음/SFX_%03d.wav" % k, 1, d.ago(300)) for k in range(d.n(60, 4))] +
          [("_공용소스/폰트/Pretendard-%d.otf" % k, .02, d.ago(500)) for k in range(d.n(9, 2))] +
          [("_공용소스/스톡/stock_%03d.mov" % k, 25, d.ago(200)) for k in range(d.n(20, 2))])

    # E: projects + card dumps
    e = P["E"]
    B = budget("E")
    project(e, "", "2025_한결증권_유튜브_숏폼", "YS", int(B * .35), 25, cam="iphone", nseq=1,
            frames=(100, 600) if S >= 1 else (20, 30))
    project(e, "", "2024_라온식품_TVC", "RF", int(B * .30), 240, cam="arri", nseq=2,
            frames=(200, 900) if S >= 1 else (20, 30), premiere=True)
    dcim_block(e, "카드백업_250312", int(B * .20), 225)
    dcim_block(e, "카드백업_251002", int(B * .15), 25)

    # F: deliveries and renders
    f = P["F"]
    B = budget("F")
    for i, (proj, short, age) in enumerate((("2025_한결증권_브랜드필름", "BF", 18),
                                           ("2025_한빛은행_디지털캠페인", "HB", 45),
                                           ("2024_라온식품_TVC", "RF", 240),
                                           ("2024_한빛은행_연말캠페인", "HY", 320),
                                           ("2024_새솔제약_런칭", "SL", 410))):
        ent = []
        for v in range(1, f.n(6, 2) + 1):
            ent.append(("납품/%s/%s_master_v%02d.mov" % (proj, short, v), 4, f.ago(age + 6 - v)))
            ent.append(("납품/%s/%s_30s_v%02d.mp4" % (proj, short, v), .4, f.ago(age + 6 - v)))
        ent.append(("납품/%s/%s_15s_최종.mp4" % (proj, short), .3, f.ago(age)))
        alloc(f, int(B * .10), ent)
        exr_sequence(f, "출력/%s/exr_master" % proj, "%s_master" % short,
                     f.rng.randint(300, 1500) if S >= 1 else 25, int(B * .055), f.ago(age + 2))
    project(f, "", "2025_다온전자_SNS", "DS", int(B * .15), 60, cam="dji", nseq=1,
            frames=(100, 400) if S >= 1 else (20, 30))
    exr_sequence(f, "출력/타이틀_png", "title", f.n(240, 12), int(B * .05), f.ago(150), "png", 5)

    # G: AE disk cache, proxies, media cache
    g = P["G"]
    B = budget("G")
    ae_disk_cache(g, "AE_Cache", int(B * .45), 3)
    media_cache(g, "Premiere/Media Cache Files", int(B * .15), 10)
    alloc(g, int(B * .30), [("Proxy_Media/%s/C%04d_Proxy.mp4" % (p, k), 1, g.ago(15 + k % 20))
                            for p in ("BF", "HB", "SS") for k in range(1, g.n(120, 4) + 1)])
    alloc(g, int(B * .10), [("Conformed Audio Files/C%04d.cfa" % k, 1, g.ago(20)) for k in
                            range(1, g.n(200, 5) + 1)] +
          [("Peak Files/C%04d.pek" % k, .02, g.ago(20)) for k in range(1, g.n(200, 5) + 1)])

    # C: system + profile + work folder
    c = P["C"]
    B = budget("C", fixed=32 * GiB + int(25.4 * GiB) + 256 * MiB + 8 * KiB + 20 * KiB + 12 * MiB)
    windows_block(c, int(B * .015))
    program_files_block(c, int(B * .06))
    user_profile(c, int(B * .40), fr)
    project(c, "작업", "2025_한결증권_브랜드필름_편집본", "BFE", int(B * .30), 16, cam="sony", nseq=1,
            frames=(100, 500) if S >= 1 else (20, 30), premiere=True)
    project(c, "작업", "2025_사내교육영상", "EDU", int(B * .225), 130, cam="iphone", nseq=1,
            frames=(100, 300) if S >= 1 else (20, 30))

    # H: archive 2023–2024 + backup of the current flagship footage (duplicates)
    h = P["H"]
    src = P["D"]
    pairs = [(rel, "백업/" + rel) for rel in sorted(src.files)
             if rel.startswith("2025_한결증권_브랜드필름/") and rel.endswith((".MP4", ".mov"))
             and "/Proxy/" not in rel][: h.n(60, 4)]
    dup = copy_entries(src, h, pairs)
    B = budget("H", fixed=dup)
    for name, short, share, age, cam in (("2024_한빛은행_연말캠페인", "HY", .18, 320, "arri"),
                                         ("2024_새솔제약_런칭", "SL", .15, 410, "sony"),
                                         ("2023_푸른항공_바이럴", "PV", .14, 690, "red"),
                                         ("2023_라온식품_브랜드필름", "RB", .13, 760, "sony"),
                                         ("2023_다온전자_전시영상", "DX", .10, 830, "gopro")):
        project(h, "아카이브", name, short, int(B * share), age, cam=cam, nseq=1,
                frames=(100, 1500) if S >= 1 else (20, 30))
    archive_zips(h, "백업/압축", int(B * .30),
                 ["2023_결산_프로젝트백업.zip", "2024_상반기_소스.7z", "2024_하반기_소스.7z",
                  "구_NAS_이관분.rar", "Windows_백업.img", "AE_프리셋_모음.zip"], 300)

    # I: older archive + camera originals (duplicates from E) + disc images
    i_ = P["I"]
    src = P["E"]
    card = sorted(rel for rel in src.files if rel.startswith("카드백업_250312/"))
    pairs = [(rel, "원본백업/" + rel) for rel in card if rel.endswith(".MP4")]
    pairs += [(rel, "원본백업/" + rel) for rel in card if "/DCIM/" in rel][: i_.n(100, 6)]
    dup = copy_entries(src, i_, pairs)
    fpairs = [(rel, "납품백업/" + rel) for rel in sorted(P["F"].files)
              if rel.startswith("납품/2024_라온식품_TVC/")][: i_.n(8, 2)]
    dup += copy_entries(P["F"], i_, fpairs)
    B = budget("I", fixed=dup)
    for name, short, share, age, cam in (("2022_한빛은행_TVC", "HT", .2, 1050, "arri"),
                                         ("2022_한결증권_사내행사", "KE", .15, 980, "sony"),
                                         ("2023_새솔제약_인터뷰", "SI", .15, 720, "iphone")):
        project(i_, "아카이브_2022-2023", name, short, int(B * share), age, cam=cam, nseq=2,
                frames=(100, 1500) if S >= 1 else (20, 30))
    archive_zips(i_, "디스크이미지", int(B * .1), ["Win10_22H2.iso", "Office2021.iso", "macOS_Ventura.dmg"],
                 800)
    archive_zips(i_, "백업", int(B * .4), ["2022_전체백업_1.zip", "2022_전체백업_2.zip",
                                         "2023_전체백업.zip", "현장촬영_원본_2022.tar"], 600)
    if team:
        add_lead_team(P, info, now, S, clips or {})
    return P, info


def add_lead_team(P, info, now, S, clips):
    """Team-named projects on the internal drives + the J / K / NAS locations (lead profile)."""
    fx = {f[0]: f for f in TEAM_FIXED}

    def T(L):
        return int(_used(info, L, FILE_RATIO[L]) * TEAM_RESERVE[L])

    d = P["D"]
    team_block(d, "Projects", d.n(14, 6), int(T("D") * .8), (3, 140), clips,
               fixed=[fx["260803_소담코스_녹차_진정크림_배너결합_KMS"], fx["소담코스_진정크림"]])
    team_block(d, "", d.n(4, 2), int(T("D") * .2), (20, 90), clips, forms=["brc", "brc_full"])
    team_block(P["E"], "", P["E"].n(8, 3), T("E"), (90, 420), clips,
               fixed=[fx["251023_한결증권_브랜드필름_편집_HYC"]])
    team_block(P["F"], "2025_작업", P["F"].n(8, 3), T("F"), (150, 420), clips,
               fixed=[fx["260115_바른기술_인터뷰_JJH"], fx["2025-11_한빛은행_런칭"]])
    team_block(P["C"], "작업", P["C"].n(4, 2), T("C"), (1, 40), clips, forms=["brc", "alias"])
    team_block(P["H"], "아카이브/2024", P["H"].n(10, 4), T("H"), (400, 760), clips)
    team_block(P["I"], "아카이브_2024", P["I"].n(6, 3), T("I"), (600, 1000), clips)
    # external / USB / NAS
    for key, lab, kind, tot, free, extra in EXTRA_DRIVES:
        P[key] = Plan(key, P["C"].rng, now, S)
        info[key] = (lab, kind, tot, free, dict(extra))
    j = P["J"]
    j.bin.append(("20251101-101010_백업_중복.mov", 12 * GiB, now - 40 * DAY))
    B = _used(info, "J", EXTRA_RATIO["J"]) - 12 * GiB
    team_block(j, "보관_2025", j.n(10, 4), int(B * .85), (200, 520), clips)
    archive_zips(j, "백업_압축", int(B * .15), ["2025_상반기_프로젝트.zip", "AE_템플릿.zip"], 250)
    k = P["K"]
    B = _used(info, "K", EXTRA_RATIO["K"])
    team_block(k, "전달용", k.n(3, 2), int(B * .6), (5, 60), clips, forms=["brc", "brc_full"])
    dcim_block(k, "카메라", int(B * .4), 15)
    nk = EXTRA_DRIVES[2][0]
    n = P[nk]
    B = _used(info, nk, NET_RATIO)
    team_block(n, "2024", n.n(8, 3), int(B * .5), (450, 800), clips)
    team_block(n, "2023", n.n(6, 2), int(B * .3), (800, 1150), clips)
    archive_zips(n, "백업_이미지", int(B * .2), ["2023_전체백업.7z", "2024_전체백업.7z",
                                            "구_외장하드_이관.zip"], 400)


def build_member_plans(rng, now, S, clips):
    """A team member's PC: C (SSD 931GB) + D (HDD 1.81TB)."""
    P = {L: Plan(L, rng, now, S) for L, *_r in MEMBER_DRIVES}
    info = {L: (lab, kind, tot, free, dict(x)) for L, lab, kind, tot, free, x in MEMBER_DRIVES}
    fx = {f[0]: f for f in TEAM_FIXED}
    c = P["C"]
    c.bin.append(("20251020-093010_화면녹화_임시.mp4", int(3.1 * GiB), now - 7 * DAY))
    fixed = 32 * GiB + int(25.4 * GiB) + 256 * MiB + 8 * KiB + 20 * KiB + 12 * MiB
    B = _used(info, "C", MEMBER_RATIO["C"]) - int(3.1 * GiB) - fixed
    windows_block(c, int(B * .03))
    program_files_block(c, int(B * .10))
    user_profile(c, int(B * .47), (10, 18))
    team_block(c, "작업", c.n(4, 2), int(B * .40), (1, 30), clips, forms=["brc", "alias"])
    d = P["D"]
    B = _used(info, "D", MEMBER_RATIO["D"])
    team_block(d, "Projects", d.n(10, 4), int(B * .55), (3, 200), clips,
               fixed=[fx["260803_소담코스_녹차_진정크림_배너결합_KMS"], fx["소담코스_진정크림"]])
    team_block(d, "2025", d.n(6, 3), int(B * .30), (200, 450), clips,
               fixed=[fx["251023_한결증권_브랜드필름_편집_HYC"], fx["2025-11_한빛은행_런칭"]])
    media_cache(d, "Media Cache Files", int(B * .15), 5)
    return P, info


# ---------------------------------------------------------------- materialize

def mark_sparse(f):
    """Windows(NTFS) 에선 truncate 만으로 희소 파일이 되지 않는다 — 실제로 디스크를 채운다
    (CI windows 러너가 「디스크 공간 부족」으로 죽었다). FSCTL_SET_SPARSE 를 먼저 건다."""
    if os.name != "nt":
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dic = k32.DeviceIoControl
    dic.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    dic.restype = wintypes.BOOL
    got = wintypes.DWORD(0)
    h = wintypes.HANDLE(msvcrt.get_osfhandle(f.fileno()))
    if not dic(h, 0x000900C4, None, 0, None, 0, ctypes.byref(got), None):   # FSCTL_SET_SPARSE
        raise OSError(ctypes.get_last_error(), "FSCTL_SET_SPARSE 실패 — 희소 파일을 만들 수 없어 중단합니다")


def _sparse(path, size, mtime):
    with open(path, "wb") as f:
        if size:
            mark_sparse(f)
            f.truncate(size)
    os.utime(path, (mtime, mtime))


def _real_bytes(root):
    """Sum of file sizes the fake backend sees (everything but .fakebin and _drive.json)."""
    tot = 0
    for dp, dns, fns in os.walk(root):
        if dp == root:
            dns[:] = [x for x in dns if x != ".fakebin"]
            fns = [x for x in fns if x != "_drive.json"]
        for fn in fns:
            tot += os.lstat(os.path.join(dp, fn)).st_size
    return tot


def write_drive(out, L, plan, info):
    lab, kind, total, free, extra = info
    root = os.path.join(out, L)
    os.makedirs(root, exist_ok=True)
    made = set()
    for rel in sorted(plan.dirs):
        os.makedirs(os.path.join(root, *rel.split("/")), exist_ok=True)
    for rel, (size, mt) in plan.files.items():
        parts = rel.split("/")
        d = os.path.join(root, *parts[:-1]) if len(parts) > 1 else root
        if d not in made:
            os.makedirs(d, exist_ok=True)
            made.add(d)
        _sparse(os.path.join(d, parts[-1]), size, mt)
    for rel, (src, mt) in plan.real.items():
        parts = rel.split("/")
        d = os.path.join(root, *parts[:-1]) if len(parts) > 1 else root
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, parts[-1])
        shutil.copyfile(src, dst)
        os.utime(dst, (mt, mt))
    binb = 0
    if plan.bin:
        bdir = os.path.join(root, ".fakebin")
        os.makedirs(bdir, exist_ok=True)
        for nm, size, mt in plan.bin:
            _sparse(os.path.join(bdir, nm), size, mt)
            binb += size
    # directory mtimes: newest child, bottom-up (so empty/unused folders look old too)
    for dp, dns, fns in os.walk(root, topdown=False):
        if dp == root:
            continue
        newest = 0.0
        for x in dns + fns:
            try:
                newest = max(newest, os.lstat(os.path.join(dp, x)).st_mtime)
            except OSError:
                pass
        if newest == 0.0:
            newest = plan.now - 400 * DAY
        os.utime(dp, (newest, newest))
    files = _real_bytes(root)
    system = (total - free) - files - binb
    if system < 0:
        raise RuntimeError("drive %s over budget by %d bytes" % (L, -system))
    meta = {"label": lab, "kind": kind, "total": total, "system_bytes": system,
            "external": bool(extra.get("external", False)),
            "has_bin": bool(extra.get("has_bin", True)),
            "network": bool(extra.get("network", False)), "unc": extra.get("unc")}
    with open(os.path.join(root, "_drive.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return {"letter": L, "files": len(plan.files) + len(plan.real), "real_files": len(plan.real),
            "real_bytes": sum(os.path.getsize(s) for s, _m in plan.real.values()),
            "file_bytes": files, "bin_bytes": binb,
            "system_bytes": system, "free": total - files - binb - system}


def write_tree(root, files, dirs=()):
    """Test helper: files = {rel: size | (size, mtime)} written sparse under root."""
    now = time.time()
    for rel in dirs:
        os.makedirs(os.path.join(root, *rel.split("/")), exist_ok=True)
    for rel, v in files.items():
        size, mt = (v, now - 3600) if isinstance(v, int) else v
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        _sparse(p, size, mt)
    return root


def _is_fake_drive_dir(p):
    return os.path.isdir(p) and os.path.isfile(os.path.join(p, "_drive.json"))


def make(out, scale="demo", seed=20251027, clean=False, now=None, profile=None, media_cache=None,
         videos=True):
    """Write the fake drives; returns one report dict per drive (key in "letter").

    profile None -> 'lead' for demo, 'classic' for small. videos=False skips ffmpeg and writes
    sparse placeholders for the render clips.
    """
    if scale not in SCALES:
        raise ValueError("scale must be one of %s" % ", ".join(SCALES))
    if profile is None:
        profile = "lead" if scale == "demo" else "classic"
    if profile not in PROFILES:
        raise ValueError("profile must be one of %s" % ", ".join(PROFILES))
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    existing = os.listdir(out)
    if existing:
        if not clean:
            raise SystemExit("출력 폴더가 비어 있지 않아요: %s (--clean 으로 기존 가짜 드라이브를 지울 수 "
                             "있어요)" % out)
        for nm in existing:
            p = os.path.join(out, nm)
            if not _is_fake_drive_dir(p):
                raise SystemExit("가짜 드라이브가 아닌 항목이 있어 지우지 않았어요: %s" % p)
        for nm in existing:
            shutil.rmtree(os.path.join(out, nm))
    rng = random.Random(seed)
    now = time.time() if now is None else now
    clips = demo_clips(media_cache) if (videos and profile != "classic") else {}
    plans, info = build_plans(rng, now, SCALES[scale], profile, clips)
    report = []
    for L in plans:
        report.append(write_drive(out, L, plans[L], info[L]))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="DriveBoard 가짜 드라이브 생성기")
    ap.add_argument("out")
    ap.add_argument("--scale", choices=sorted(SCALES), default="demo")
    ap.add_argument("--profile", choices=PROFILES, default=None,
                    help="lead(팀장 PC: 내장 7 + 외장 J·USB K·NAS) / member(C+D) / classic(내장 7). "
                         "기본: demo=lead, small=classic")
    ap.add_argument("--no-video", action="store_true", help="ffmpeg 로 실제 영상을 만들지 않는다")
    ap.add_argument("--seed", type=int, default=20251027)
    ap.add_argument("--clean", action="store_true",
                    help="이미 있는 가짜 드라이브(_drive.json 이 있는 폴더)를 지우고 다시 만든다")
    a = ap.parse_args(argv)
    t0 = time.time()
    rep = make(a.out, a.scale, a.seed, a.clean, profile=a.profile, videos=not a.no_video)
    nfiles = sum(r["files"] for r in rep)
    for r in rep:
        print("%-8s files=%6d  data=%8.2f GiB  bin=%7.2f GiB  system=%7.2f GiB  free=%8.2f GiB"
              "  real=%d (%.1f MB)" % (
                  r["letter"], r["files"], r["file_bytes"] / GiB, r["bin_bytes"] / GiB,
                  r["system_bytes"] / GiB, r["free"] / GiB, r["real_files"],
                  r["real_bytes"] / MiB))
    print("total files: %d  (%.1fs)" % (nfiles, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
