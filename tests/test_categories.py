"""Classification vectors written by hand from SPEC §3 (not generated from the code)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import categories as C  # noqa: E402
from core.categories import Classifier  # noqa: E402

ROOT = os.path.join(os.sep, "fake", "D")


def cat_of(rel, settings=None):
    """Category key of file `rel` (forward slashes, relative to ROOT)."""
    cls = Classifier(settings or {})
    parts = rel.split("/")
    ctx = cls.root_ctx(ROOT)
    path = ROOT
    for i, comp in enumerate(parts[:-1]):
        path = os.path.join(path, comp)
        ctx = cls.dir_ctx(ctx, comp, path, i + 1)
    return C.ID_TO_KEY[cls.file_cat(parts[-1], ctx, len(parts) - 1)]


def P(rel):
    return os.path.join(ROOT, *rel.split("/"))


# (path relative to the drive root, expected category key)
VECTORS = [
    # --- camera file name patterns (no folder context)
    ("misc/C0001.MP4", "source"),
    ("misc/c0123.mp4", "source"),
    ("misc/C00012.MP4", "video"),                 # 5 digits: not the Sony pattern
    ("misc/A001C003_220101_R1AB.mov", "source"),  # ARRI
    ("misc/A001_C003_0312XY.mov", "source"),      # RED-like
    ("misc/GH010123.MP4", "source"),
    ("misc/GX010045.mp4", "source"),
    ("misc/GOPR0042.MP4", "source"),
    ("misc/DJI_0001.MP4", "source"),
    ("misc/DJI_20250312101010_0001_D.MP4", "source"),
    ("misc/MVI_1234.MOV", "source"),
    ("misc/IMG_1234.MOV", "source"),
    ("misc/IMG_123.MOV", "video"),                # 3 digits
    ("misc/PXL_20250312_101010123.mp4", "source"),
    ("misc/VID_20250312_101010.mp4", "source"),
    ("misc/20250312_101010.mp4", "source"),
    ("misc/20250312_1010.mp4", "video"),
    ("misc/interview.mp4", "video"),
    ("misc/clip.r3d", "source"),                  # RAW extensions
    ("misc/clip.braw", "source"),
    ("misc/clip.ari", "source"),
    ("misc/clip.MXF", "source"),
    ("misc/A001C001_final.mov", "source"),        # camera pattern beats name heuristics
    # --- proxies
    ("misc/C0001_Proxy.mp4", "proxy"),            # suffix beats camera pattern
    ("misc/interview-proxy.mov", "proxy"),
    ("misc/interview proxy.mov", "proxy"),
    ("misc/shot_proxy_v2.mp4", "proxy"),
    ("misc/proxyclip.mp4", "video"),
    ("proj/Proxy/C0001.MP4", "proxy"),
    ("proj/proxies/a.mov", "proxy"),
    ("proj/프록시/a.mp4", "proxy"),
    ("misc/GX010045.LRV", "proxy"),
    ("proj/footage/GX010045.LRV", "proxy"),
    ("proj/Proxy/ref.jpg", "proxy"),
    ("proj/footage/Proxy/a.mp4", "proxy"),
    # --- render name heuristics
    ("misc/final_v03.mp4", "render"),
    ("misc/promo_v2.mp4", "render"),
    ("misc/promo v12.mov", "render"),
    ("misc/promo_v2_alt.mp4", "video"),
    ("misc/BF_comp_v01.mp4", "render"),
    ("misc/광고_최종.mp4", "render"),
    ("misc/광고_완성.mp4", "render"),
    ("misc/광고_렌더.mp4", "render"),
    ("misc/export_30s.mp4", "render"),
    ("misc/Render_01.mov", "render"),
    ("misc/C0001_final.mp4", "render"),
    # --- render folders
    ("proj/render/C0001.MP4", "render"),          # render context beats camera pattern
    ("proj/Renders/a.mov", "render"),
    ("proj/output/a.mp4", "render"),
    ("proj/exports/a.mp4", "render"),
    ("proj/delivery/a.mp4", "render"),
    ("proj/deliverables/a.mp4", "render"),
    ("proj/Final/a.mp4", "render"),
    ("proj/out/a.mp4", "render"),
    ("proj/출력/a.mp4", "render"),
    ("proj/납품/a.mp4", "render"),
    ("proj/완성본/a.mp4", "render"),
    ("proj/최종/a.mp4", "render"),
    ("proj/06_render/a.mp4", "render"),
    ("proj/AE렌더_모음/a.mp4", "render"),
    ("proj/2차출력물/a.mp4", "render"),
    ("proj/outputs_old/a.mp4", "video"),
    ("proj/render/exr/shot010_0001.exr", "render"),   # image inside a render folder
    ("proj/render/frame.png", "render"),
    ("proj/render/notes.txt", "other"),
    ("proj/render/BGM.wav", "audio"),
    ("proj/render/proj.aep", "project"),
    ("proj/render/a.psd", "project"),
    # --- source folders
    ("proj/footage/interview.mov", "source"),
    ("proj/Footages/a.mp4", "source"),
    ("proj/src/a.mp4", "source"),
    ("proj/RAW/a.mp4", "source"),
    ("proj/rushes/a.mp4", "source"),
    ("proj/원본/a.mp4", "source"),
    ("proj/촬영본/a.mp4", "source"),
    ("proj/DCIM/100MSDCF/DSC01234.JPG", "source"),
    ("card/PRIVATE/M4ROOT/CLIP/C0001.MP4", "source"),
    ("proj/01_footage/a.mov", "source"),
    ("proj/2025_촬영_현장A/a.mp4", "source"),
    ("proj/footage/render/a.mp4", "render"),      # child name overrides the parent
    ("proj/render/footage/a.mp4", "source"),
    ("proj/footage/a.dng", "source"),
    ("proj/footage/a.psb", "project"),
    ("proj/footage/a.wav", "audio"),
    ("proj/footage/a.xml", "other"),
    # --- archive / auto-save
    ("proj/Adobe After Effects Auto-Save/proj auto-save 1.aep", "archive"),
    ("proj/Adobe Premiere Pro Auto-Save/a.prproj", "archive"),
    ("proj/backup/a.mp4", "archive"),
    ("proj/백업/a.zip", "archive"),
    ("proj/backups/notes.txt", "archive"),
    ("proj/autosave/a.wav", "audio"),
    ("proj/backup/photo.jpg", "image"),
    ("backup/setup.exe", "archive"),
    ("misc/a.zip", "archive"),
    ("misc/a.7z", "archive"),
    ("misc/a.iso", "archive"),
    ("misc/a.bak", "archive"),
    ("misc/archive.tar.gz", "archive"),
    # --- cache extensions and folders
    ("misc/a.cfa", "cache"),
    ("misc/a.pek", "cache"),
    ("misc/x.ims", "cache"),
    ("misc/db.mcdb", "cache"),
    ("misc/a.prmdc", "cache"),
    ("misc/a.aecache", "cache"),
    ("misc/a.mcache", "cache"),
    ("proj/render/a.cfa", "cache"),
    ("Media Cache Files/C0001 48000.cfa", "cache"),
    ("Media Cache Files/thing.mp4", "cache"),
    ("proj/Media Cache/x.mp4", "cache"),
    ("Peak Files/x.wav", "cache"),
    ("AE/Disk Cache/x.dat", "cache"),
    ("AE/Disk Cache - STUDIO-EDIT01/00/x.dat", "cache"),
    ("aecache/x.bin", "cache"),
    ("Conformed Audio Files/x.wav", "cache"),
    ("proj/temp/x.tmp", "other"),                 # temp only under AppData\Local
    ("Users/kim/AppData/Local/Temp/x.mp4", "cache"),
    ("Users/kim/AppData/Local/Temp/AdobeLogs/x.log", "cache"),
    ("Users/kim/AppData/Local/Temp/a.aep", "cache"),
    ("Users/kim/AppData/Roaming/Adobe/Common/Media Cache Files/a.cfa", "cache"),
    ("Users/kim/AppData/Roaming/Adobe/Common/Media Cache Files/a.mp4", "cache"),
    ("Backup/Users/kim/AppData/Local/Temp/x.mp4", "cache"),
    # --- AppData (app) and user folders
    ("Users/kim/AppData/Local/Adobe/After Effects/25.0/prefs.txt", "app"),
    ("Users/kim/AppData/Roaming/Adobe/render/a.mp4", "app"),
    ("Users/kim/AppData/LocalLow/x.dat", "app"),
    ("Users/kim/AppData/Local/x.mp4", "app"),
    ("Users/kim/AppData/Roaming/Temp/x.mp4", "app"),
    ("Users/kim/Desktop/project/footage/a.mp4", "source"),
    ("Users/kim/Documents/a.aep", "project"),
    ("Backup/Users/kim/AppData/Local/x.mp4", "archive"),   # not a real profile path
    # --- Windows / system
    ("Windows/System32/a.dll", "app"),
    ("Windows/Temp/x.mp4", "app"),
    ("Windows/notes.txt", "app"),
    ("Program Files/Adobe/AfterFX.exe", "app"),
    ("Program Files/Adobe/render/a.mp4", "app"),
    ("Program Files (x86)/Common/a.dll", "app"),
    ("ProgramData/Adobe/Media Cache Files/a.mp4", "app"),
    ("ProgramData/Adobe/Media Cache Files/a.cfa", "cache"),   # cache extension rule is first
    ("Recovery/x.wim", "app"),
    ("Boot/BCD", "app"),
    ("EFI/Microsoft/boot.efi", "app"),
    ("$WINDOWS.~BT/x.esd", "app"),
    ("Windows.old/x.dll", "app"),
    ("$WinREAgent/x.dat", "app"),
    ("pagefile.sys", "app"),
    ("hiberfil.sys", "app"),
    ("swapfile.sys", "app"),
    ("DumpStack.log.tmp", "app"),
    ("misc/DumpStack.log.tmp", "other"),          # only at the drive root
    ("sub/Windows/a.mp4", "video"),               # only directly below the root
    ("misc/setup.exe", "app"),
    ("misc/plugin.aex", "app"),
    ("misc/a.msi", "app"),
    ("misc/x.cab", "app"),
    # --- projects
    ("misc/a.aep", "project"),
    ("misc/a.prproj", "project"),
    ("misc/a.psd", "project"),
    ("misc/a.psb", "project"),
    ("misc/a.ai", "project"),
    ("misc/a.c4d", "project"),
    ("misc/a.blend", "project"),
    ("misc/a.nk", "project"),
    ("misc/a.drp", "project"),
    ("misc/a.mogrt", "project"),
    ("misc/a.ffx", "project"),
    # --- images / audio / other
    ("misc/a.jpg", "image"),
    ("misc/a.EXR", "image"),
    ("misc/a.dng", "image"),
    ("misc/a.heic", "image"),
    ("misc/a.wav", "audio"),
    ("misc/a.FLAC", "audio"),
    ("misc/a.bwf", "audio"),
    ("Media Cache Files/a.wav", "cache"),
    ("misc/README", "other"),
    ("misc/.DS_Store", "other"),
    ("misc/a.pdf", "other"),
]


class TestTable(unittest.TestCase):
    def test_categories_table(self):
        self.assertEqual(C.NCAT, 11)
        self.assertEqual(len(C.CATEGORIES), 11)
        keys = ["cache", "render", "source", "proxy", "video", "project", "image", "audio",
                "archive", "app", "other"]
        colors = ["#FF9F0A", "#2FD27F", "#0A84FF", "#30D5C8", "#6E6BFF", "#BF5AF2", "#FF4F8B",
                  "#FFD60A", "#C0A37A", "#8E8E93", "#5C5C63"]
        for i, c in enumerate(C.CATEGORIES):
            self.assertEqual(c["id"], i)
            self.assertEqual(c["key"], keys[i])
            self.assertEqual(c["color"], colors[i])
            self.assertEqual(c["deletable"], keys[i] != "app")
            self.assertTrue(c["name"] and c["desc"])
            self.assertEqual(set(c), {"id", "key", "name", "color", "deletable", "desc"})
        self.assertEqual(C.CATEGORIES[9]["name"], "앱·시스템")
        self.assertEqual(C.VIRTUAL["bin"]["color"], "#FF453A")
        self.assertEqual(C.VIRTUAL["unscanned"]["color"], "#3A3A40")
        self.assertEqual(C.VIRTUAL["unscanned"]["name"], "시스템·확인 불가")

    def test_cat_id(self):
        self.assertEqual(C.cat_id("render"), 1)
        self.assertEqual(C.cat_id(9), 9)
        self.assertEqual(C.cat_id("3"), 3)
        self.assertIsNone(C.cat_id(11))
        self.assertIsNone(C.cat_id(True))
        self.assertIsNone(C.cat_id("nope"))

    def test_split_ext(self):
        self.assertEqual(C.split_ext("a.tar.GZ"), ("a.tar", "gz"))
        self.assertEqual(C.split_ext("README"), ("README", ""))


class TestVectors(unittest.TestCase):
    def test_count(self):
        self.assertGreaterEqual(len(VECTORS), 90)

    def test_vectors(self):
        for rel, want in VECTORS:
            with self.subTest(rel=rel):
                self.assertEqual(cat_of(rel), want)


class TestRules(unittest.TestCase):
    def test_rule_is_absolute(self):
        s = {"rules": [{"path": P("proj"), "cat": "archive"}]}
        self.assertEqual(cat_of("proj/footage/C0001.MP4", s), "archive")
        self.assertEqual(cat_of("proj/a.cfa", s), "archive")          # beats cache extension
        self.assertEqual(cat_of("proj/Media Cache Files/x.mp4", s), "archive")
        self.assertEqual(cat_of("proj/x.exe", s), "archive")
        self.assertEqual(cat_of("other/C0001.MP4", s), "source")     # outside the rule

    def test_deepest_rule_wins_regardless_of_order(self):
        rules = [{"path": P("proj/render"), "cat": 10}, {"path": P("proj"), "cat": "archive"}]
        for rr in (rules, list(reversed(rules))):
            s = {"rules": rr}
            self.assertEqual(cat_of("proj/render/x.mp4", s), "other")
            self.assertEqual(cat_of("proj/render/deep/x.wav", s), "other")
            self.assertEqual(cat_of("proj/footage/a.mp4", s), "archive")

    def test_rule_on_system_folder_and_root(self):
        self.assertEqual(cat_of("Windows/a.dll", {"rules": [{"path": P("Windows"), "cat": 10}]}),
                         "other")
        self.assertEqual(cat_of("Windows/a.dll", {"rules": [{"path": ROOT, "cat": "other"}]}),
                         "other")

    def test_rule_path_normalized(self):
        s = {"rules": [{"path": P("proj") + os.sep, "cat": "proxy"}]}
        self.assertEqual(cat_of("proj/a.mov", s), "proxy")
        s = {"rules": [{"path": P("proj/./x/.."), "cat": 3}]}
        self.assertEqual(cat_of("proj/a.mov", s), "proxy")

    def test_bad_rules_ignored(self):
        s = {"rules": [{"path": P("proj"), "cat": 99}, {"path": "", "cat": 1}, "junk",
                       {"cat": 1}]}
        self.assertEqual(cat_of("proj/C0001.MP4", s), "source")

    def test_cache_paths(self):
        s = {"cache_paths": [P("work/AE_Cache")]}
        self.assertEqual(cat_of("work/AE_Cache/x.mp4", s), "cache")
        self.assertEqual(cat_of("work/AE_Cache/sub/render/a.mov", s), "cache")
        self.assertEqual(cat_of("work/AE_Cache/a.aep", s), "cache")
        self.assertEqual(cat_of("work/other/a.mov", s), "video")

    def test_rule_inside_cache_path_and_reverse(self):
        s = {"cache_paths": [P("c")], "rules": [{"path": P("c/keep"), "cat": "source"}]}
        self.assertEqual(cat_of("c/keep/a.mp4", s), "source")
        self.assertEqual(cat_of("c/a.mp4", s), "cache")
        s = {"rules": [{"path": P("r"), "cat": "render"}], "cache_paths": [P("r/cache")]}
        self.assertEqual(cat_of("r/cache/a.cfa", s), "render")       # rules have priority

    def test_ctx_for_matches_walk(self):
        cls = Classifier({})
        ctx, depth = cls.ctx_for(P("Users/kim/AppData/Local/Temp"), ROOT)
        self.assertEqual(depth, 5)
        self.assertEqual(C.ID_TO_KEY[cls.file_cat("a.mp4", ctx, depth)], "cache")
        with self.assertRaises(ValueError):
            cls.ctx_for(os.path.join(os.sep, "elsewhere"), ROOT)

    def test_rel_parts(self):
        self.assertEqual(C.rel_parts(ROOT, ROOT), [])
        self.assertEqual(C.rel_parts(ROOT, P("a/b")), ["a", "b"])
        self.assertIsNone(C.rel_parts(ROOT, ROOT + "x"))
        self.assertIsNone(C.rel_parts(ROOT, "relative/path"))
        self.assertEqual(C.rel_parts(ROOT + os.sep, P("a")), ["a"])


if __name__ == "__main__":
    unittest.main()
