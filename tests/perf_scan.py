#!/usr/bin/env python3
"""Scanner throughput / memory check (not a unittest).

    python tests/perf_scan.py [--dir DIR] [--files 300000] [--dirs 30000] [--keep]

Builds a synthetic tree (empty + a few sparse big files, image sequences, caches) in a
child process, scans it in this process and prints files/sec and peak RSS.
Target (SPEC §5): extrapolates to 1M files in < 60s and < 1GB.
"""

import argparse
import math
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def build_tree(root, nfiles, ndirs):
    """Child process: ndirs leaf folders (2 levels) with nfiles spread over them."""
    os.makedirs(root, exist_ok=True)
    top = max(1, int(math.ceil(ndirs ** 0.5)))
    per_top = max(1, int(math.ceil(ndirs / float(top))))
    per_dir = max(1, int(math.ceil(nfiles / float(top * per_top))))
    made = 0
    for t in range(top):
        tdir = os.path.join(root, "proj_%03d" % t)
        for j in range(per_top):
            kind = j % 10
            name = ("footage", "render", "Media Cache Files", "aep", "exr", "proxy", "audio",
                    "image", "misc", "backup")[kind]
            d = os.path.join(tdir, "%s_%04d" % (name, j))
            os.makedirs(d)
            for k in range(per_dir):
                if kind == 4:
                    fn = "shot_%04d.exr" % k
                elif kind == 0:
                    fn = "C%04d.MP4" % k
                elif kind == 2:
                    fn = "C%04d 48000.cfa" % k
                else:
                    fn = "file_%05d.%s" % (k, ("mov", "aep", "wav", "png", "txt", "zip")[k % 6])
                fd = os.open(os.path.join(d, fn), os.O_CREAT | os.O_WRONLY, 0o644)
                if k % 50 == 0:
                    os.ftruncate(fd, (60 + k) << 20)      # sparse big file
                os.close(fd)
                made += 1
    return made


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.environ.get("DRIVEBOARD_PERF_DIR"))
    ap.add_argument("--files", type=int, default=300000)
    ap.add_argument("--dirs", type=int, default=30000)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--_build", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a._build:
        print(build_tree(a._build, a.files, a.dirs))
        return 0

    base = a.dir or tempfile.mkdtemp(prefix="driveboard-perf-")
    root = os.path.join(base, "P")
    if not os.path.isdir(root):
        t = time.time()
        out = subprocess.run([sys.executable, os.path.abspath(__file__), "--_build", root,
                              "--files", str(a.files), "--dirs", str(a.dirs)],
                             check=True, capture_output=True, text=True).stdout.strip()
        print("built %s files in %.1fs at %s" % (out, time.time() - t, root))

    from core import model, scanner
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    marks = {}

    def cb(ev):
        marks.setdefault(ev["phase"], time.time())
    t0 = time.time()
    h = scanner.start_scan({"letter": "P", "root": root}, {}, cb)
    h.join()
    dt = time.time() - t0
    idx = h.result
    if idx is None:
        print("scan failed:", h.error)
        return 1
    rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    files = idx.d_files[0]
    dirs = idx.ndirs - 1
    fin = (marks.get("done", t0 + dt) - marks["finalize"]) if "finalize" in marks else 0.0
    t = time.time()
    errs = model.check_invariants(idx)
    tchk = time.time() - t
    rate = files / dt if dt else float("inf")
    print("scanned files=%d dirs=%d items=%d in %.2fs (finalize %.2fs)" % (
        files, dirs, idx.nitems, dt, fin))
    print("rate %.0f files/s  ->  1M files ~ %.1fs" % (rate, 1e6 / rate))
    print("peak RSS %.1f MB (before scan %.1f MB, delta %.1f MB) ; 1M files ~ %.0f MB"
          % (rss1 / 1024.0, rss0 / 1024.0, (rss1 - rss0) / 1024.0,
             rss0 / 1024.0 + (rss1 - rss0) / 1024.0 * (1e6 / max(1, files))))
    print("invariants: %s (%.2fs)" % ("ok" if not errs else errs[:3], tchk))
    if not a.keep and not a.dir:
        shutil.rmtree(base, ignore_errors=True)
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
