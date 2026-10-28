"""core/projects.py: 프로젝트 판정·레코드·묶기·영상 목록 (기대값은 손으로 적었다)."""

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from core import categories as C  # noqa: E402
from core import model, projects, scanner  # noqa: E402
from make_fake_drives import write_tree  # noqa: E402

MB = 1 << 20


def ts(y, m, d):
    return time.mktime((y, m, d, 12, 0, 0, 0, 0, -1))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dbtest-proj-")
        self.root = os.path.join(self.tmp, "D")
        os.makedirs(self.root)
        self.drive = {"letter": "D", "root": self.root}
        T = ts
        write_tree(self.root, {
            # 1. BRC 정식 이름 + 하위 날짜 폴더(부모에 속함)
            "260803_소담코스_녹차_진정크림_배너결합_KMS/render/소담코스_15s_v02.mp4": (5 * MB, T(2026, 8, 5)),
            "260803_소담코스_녹차_진정크림_배너결합_KMS/render/소담코스_15s_v01.mp4": (5 * MB, T(2026, 8, 4)),
            "260803_소담코스_녹차_진정크림_배너결합_KMS/260805_수정본/수정.mp4": (2 * MB, T(2026, 8, 9)),
            # 2. render > source (source 가 더 최신이어도)
            "251023_한결증권_브랜드필름_편집_HYC/aep/BF_v01.aep": (2 * MB, T(2025, 10, 20)),
            "251023_한결증권_브랜드필름_편집_HYC/render/BF_v01.mp4": (3 * MB, T(2025, 10, 22)),
            "251023_한결증권_브랜드필름_편집_HYC/footage/C0001.MP4": (90 * MB, T(2025, 10, 25)),
            "251023_한결증권_브랜드필름_편집_HYC/Proxy/C0001_Proxy.mp4": (1 * MB, T(2025, 10, 26)),
            # 3. aep 직속, 광고주 표기가 정본과 공백만 다름
            "한결 증권_런칭/런칭.aep": (2 * MB, T(2025, 6, 1)),
            # 4. aep 한 단계 아래, 별칭(대소문자 무시)
            "Kiwoom_SNS/aep/sns.aepx": (2 * MB, T(2025, 7, 1)),
            # 5. project_roots 의 직속 자식 (날짜 없음 → mtime)
            "Projects/소담코스_진정크림/doc/a.pdf": (3 * MB, T(2025, 3, 14)),
            "Projects/잡동사니/a.txt": (10, T(2025, 3, 1)),                  # 1MB 미만
            # 6. YYYY-MM
            "2025-11_한빛은행_런칭/render/HB.mov": (4 * MB, T(2025, 11, 3)),
            # 7. 상위 폴더가 프로젝트가 아니면 더 내려간다 + 앞 연도 토큰
            "작업/2025_다온전자_SNS/aep/x.prproj": (2 * MB, T(2025, 5, 5)),
            "작업/deep/a/b/aep/x.drp": (2 * MB, T(2025, 5, 6)),
            # 8. 시스템·캐시 문맥은 제외
            "Users/kim/AppData/Local/260101_캐시/x.mp4": (5 * MB, T(2026, 1, 1)),
            "Media Cache Files/260101_x/y.mp4": (5 * MB, T(2026, 1, 1)),
            "Windows/260101_sys/x.mp4": (5 * MB, T(2026, 1, 1)),
            # 9. 제외 폴더: 그 폴더는 아니고 안쪽은 본다
            "260202_제외됨/260203_안쪽/x.mov": (2 * MB, T(2026, 2, 3)),
            # 10. 없는 날짜는 날짜가 아니다
            "260230_잘못된날짜/x.mov": (2 * MB, T(2026, 2, 1)),
            # 11. 작업자·작업종류, 영상 없음
            "보관/소담코스_녹차_JJH_@BC/aep/a.aep": (2 * MB, T(2024, 12, 24)),
        })
        self.settings = {
            "project_roots": [os.path.join(self.root, "Projects")],
            "project_excluded": [os.path.join(self.root, "260202_제외됨")],
            "client_aliases": {"KIWOOM": "한결증권"},
            "workers": {"JJH": "이서연", "kms": "홍길동"},
        }
        self.idx = scanner.scan_subtree(self.root, self.drive, {})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def P(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def detect(self, **over):
        s = dict(self.settings, **over)
        return {os.path.relpath(p["path"], self.root).replace(os.sep, "/"): p
                for p in projects.detect_projects(self.idx, s)}


class TestDetect(Base):
    def test_which_folders(self):
        got = self.detect()
        self.assertEqual(sorted(got), sorted([
            "260803_소담코스_녹차_진정크림_배너결합_KMS", "251023_한결증권_브랜드필름_편집_HYC",
            "한결 증권_런칭", "Kiwoom_SNS", "Projects/소담코스_진정크림", "2025-11_한빛은행_런칭",
            "작업/2025_다온전자_SNS", "작업/deep/a/b", "260202_제외됨/260203_안쪽",
            "보관/소담코스_녹차_JJH_@BC"]))

    def test_record_brc(self):
        p = self.detect()["260803_소담코스_녹차_진정크림_배너결합_KMS"]
        did = model.find_dir(self.idx, self.P("260803_소담코스_녹차_진정크림_배너결합_KMS"))
        self.assertEqual(p["key"], "D:%d" % did)
        self.assertEqual((p["drive"], p["did"], p["name"]),
                         ("D", did, "260803_소담코스_녹차_진정크림_배너결합_KMS"))
        self.assertEqual((p["date"], p["date_source"], p["year"], p["month"]),
                         ("2026-08-03", "name", 2026, 8))
        self.assertEqual((p["client"], p["client_raw"], p["title"]),
                         ("소담코스", "소담코스", "녹차 진정크림 배너결합"))
        self.assertEqual((p["worker"], p["worker_name"], p["job_kind"]), ("KMS", "홍길동", ""))
        self.assertEqual((p["size"], p["files"]), (12 * MB, 3))
        self.assertEqual(sum(p["cat_bytes"]), 12 * MB)
        self.assertEqual(p["newest"], ts(2026, 8, 9))
        # render 폴더의 최신이 대표 (하위 날짜 폴더의 더 최신 영상은 '기타 영상')
        self.assertEqual(p["preview"], {
            "path": self.P("260803_소담코스_녹차_진정크림_배너결합_KMS/render/소담코스_15s_v02.mp4"),
            "name": "소담코스_15s_v02.mp4", "size": 5 * MB, "mtime": ts(2026, 8, 5), "cat": C.RENDER})
        self.assertEqual(p["videos"], 2)        # render·수정본 각 1 (작은 v01 은 인덱스가 모른다)

    def test_render_beats_newer_source_and_proxy_ignored(self):
        p = self.detect()["251023_한결증권_브랜드필름_편집_HYC"]
        self.assertEqual(p["preview"]["name"], "BF_v01.mp4")
        self.assertEqual(p["videos"], 2)            # render + footage (proxy 제외)
        self.assertEqual((p["date"], p["client"], p["title"], p["worker"], p["worker_name"]),
                         ("2025-10-23", "한결증권", "브랜드필름 편집", "HYC", ""))

    def test_alias_and_spacing(self):
        got = self.detect()
        self.assertEqual(got["한결 증권_런칭"]["client"], "한결증권")      # 정본 표기와 공백만 다름
        self.assertEqual(got["한결 증권_런칭"]["client_raw"], "한결 증권")
        self.assertEqual(got["Kiwoom_SNS"]["client"], "한결증권")          # 별칭(대소문자 무시)
        self.assertEqual(got["Kiwoom_SNS"]["title"], "SNS")
        got = self.detect(client_aliases={})
        self.assertEqual(got["Kiwoom_SNS"]["client"], "Kiwoom")
        self.assertEqual(got["한결 증권_런칭"]["client"], "한결 증권")

    def test_spelling_unified_without_alias(self):
        write_tree(self.root, {"kiwoom_유튜브/a.aep": (2 * MB, ts(2025, 1, 1)),
                               "KIWOOM_이벤트/a.aep": (2 * MB, ts(2025, 1, 2)),
                               "Kiwoom_SNS2/a.aep": (2 * MB, ts(2025, 1, 3)),
                               "Ｋｉｗｏｏｍ_전각/a.aep": (2 * MB, ts(2025, 1, 4))})
        self.idx = scanner.scan_subtree(self.root, self.drive, {})
        got = self.detect(client_aliases={})
        names = {got[k]["client"] for k in ("kiwoom_유튜브", "KIWOOM_이벤트", "Kiwoom_SNS2",
                                            "Kiwoom_SNS", "Ｋｉｗｏｏｍ_전각")}
        self.assertEqual(names, {"Kiwoom"})                 # 가장 많이 쓴 표기 (2 : 1 : 1 : 1)

    def test_mtime_fallback_and_lead_year(self):
        got = self.detect()
        p = got["Projects/소담코스_진정크림"]
        self.assertEqual((p["date"], p["date_source"], p["year"], p["month"]),
                         ("2025-03-14", "mtime", 2025, 3))
        self.assertEqual((p["client"], p["title"], p["preview"], p["videos"]),
                         ("소담코스", "진정크림", None, 0))
        p = got["작업/2025_다온전자_SNS"]
        self.assertEqual((p["client"], p["title"], p["date_source"], p["date"]),
                         ("다온전자", "SNS", "mtime", "2025-05-05"))
        p = got["2025-11_한빛은행_런칭"]
        self.assertEqual((p["date"], p["year"], p["month"], p["client"], p["title"]),
                         ("2025-11", 2025, 11, "한빛은행", "런칭"))
        self.assertEqual(p["preview"]["cat"], C.RENDER)
        p = got["작업/deep/a/b"]
        self.assertEqual((p["client"], p["title"]), ("b", "b"))

    def test_worker_job_kind_and_unsorted(self):
        p = self.detect()["보관/소담코스_녹차_JJH_@BC"]
        self.assertEqual((p["worker"], p["worker_name"], p["job_kind"], p["client"], p["title"]),
                         ("JJH", "이서연", "@BC", "소담코스", "녹차"))
        write_tree(self.root, {"260101/a.mov": (2 * MB, ts(2026, 1, 1)),
                               "260102_@MB/a.mov": (2 * MB, ts(2026, 1, 2))})
        self.idx = scanner.scan_subtree(self.root, self.drive, {})
        got = self.detect()
        self.assertEqual(got["260101"]["client"], projects.UNSORTED)
        self.assertEqual(got["260101"]["title"], "260101")
        self.assertEqual((got["260102_@MB"]["client"], got["260102_@MB"]["job_kind"]),
                         (projects.UNSORTED, "@MB"))

    def test_settings_workers_extend_initials(self):
        write_tree(self.root, {"보관/소담코스_LSH/aep/a.aep": (2 * MB, ts(2025, 1, 1))})
        self.idx = scanner.scan_subtree(self.root, self.drive, {})
        p = self.detect()["보관/소담코스_LSH"]
        self.assertEqual((p["worker"], p["title"]), ("", "LSH"))      # 일반 토큰 2개: 작업자 자리 없음
        p = self.detect(workers={"LSH": "이수현"})["보관/소담코스_LSH"]
        self.assertEqual((p["worker"], p["worker_name"], p["client"], p["title"]),
                         ("LSH", "이수현", "소담코스", "소담코스 LSH"))

    def test_container_folder_is_not_a_project(self):
        # 모음 폴더 안의 프로젝트마다 aep 가 직속이면 「한 단계 아래 aep」 로 모음 폴더가 프로젝트가 되면 안 된다
        write_tree(self.root, {
            "모음/261027_소담코스_청귤_KMS/261027_소담코스_청귤_KMS.aep": (2 * MB, ts(2026, 10, 27)),
            "모음2/소담코스_런칭/런칭.aep": (2 * MB, ts(2025, 1, 1)),           # 날짜 없는 프로젝트 둘
            "모음2/한결_SNS/sns.aep": (2 * MB, ts(2025, 1, 2)),
            "도구폴더/AE/x.aep": (2 * MB, ts(2025, 2, 1)),                  # 프로젝트 안의 AE·PR 폴더
            "도구폴더/PR/x.prproj": (2 * MB, ts(2025, 2, 2)),
        })
        self.idx = scanner.scan_subtree(self.root, self.drive, {})
        got = self.detect()
        self.assertIn("모음/261027_소담코스_청귤_KMS", got)
        self.assertNotIn("모음", got)
        self.assertIn("모음2/소담코스_런칭", got)
        self.assertIn("모음2/한결_SNS", got)
        self.assertNotIn("모음2", got)
        self.assertIn("도구폴더", got)
        self.assertNotIn("도구폴더/AE", got)

    def test_roots_excluded_and_contexts(self):
        got = self.detect(project_roots=[], project_excluded=[])
        self.assertNotIn("Projects/소담코스_진정크림", got)
        self.assertIn("260202_제외됨", got)
        self.assertNotIn("260202_제외됨/260203_안쪽", got)     # top-most wins again
        got = self.detect(project_excluded=[self.P("260803_소담코스_녹차_진정크림_배너결합_KMS")])
        self.assertIn("260803_소담코스_녹차_진정크림_배너결합_KMS/260805_수정본", got)
        # 사용자 규칙으로 캐시 문맥이 된 폴더도 제외
        got2 = {os.path.relpath(p["path"], self.root).replace(os.sep, "/")
                for p in projects.detect_projects(
                    self.idx, self.settings,
                    C.Classifier({"rules": [{"path": self.P("보관"), "cat": "cache"}]}))}
        self.assertNotIn("보관/소담코스_녹차_JJH_@BC", got2)
        self.assertEqual(projects.detect_projects(model.new_index("E", "/nowhere"), {}), [])

    def test_after_graft_and_detach(self):
        proj = self.P("260803_소담코스_녹차_진정크림_배너결합_KMS")
        write_tree(proj, {"render/소담코스_15s_v03.mp4": (1, ts(2026, 8, 20))})
        model.graft(self.idx, scanner.scan_subtree(proj, self.drive, {}), proj)
        p = self.detect()["260803_소담코스_녹차_진정크림_배너결합_KMS"]
        self.assertEqual(p["preview"]["name"], "소담코스_15s_v03.mp4")
        model.detach(self.idx, proj)
        self.assertNotIn("260803_소담코스_녹차_진정크림_배너결합_KMS", self.detect())


class TestGroup(Base):
    def setUp(self):
        super().setUp()
        self.ps = projects.detect_projects(self.idx, self.settings)

    def test_by_client(self):
        r = projects.group_projects(self.ps, "client")
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 10)
        self.assertEqual(r["total_bytes"], sum(p["size"] for p in self.ps))
        titles = [g["title"] for g in r["groups"]]
        # 한결증권: 96 + 2 + 2 MB ; 소담코스: 12 + 3 + 2 ; 한빛은행 4 ; 다온전자 2 ; b 2 ; 안쪽 2
        self.assertEqual(titles[:3], ["한결증권", "소담코스", "한빛은행"])
        k = r["groups"][0]
        self.assertEqual((k["count"], k["bytes"], k["subtitle"]), (3, 100 * MB, "프로젝트 3개"))
        self.assertEqual(sum(k["cat_bytes"]), 100 * MB)
        self.assertEqual([p["name"] for p in k["projects"]],
                         ["251023_한결증권_브랜드필름_편집_HYC", "Kiwoom_SNS", "한결 증권_런칭"])
        self.assertEqual(r["clients"][0], {"name": "한결증권", "count": 3, "bytes": 100 * MB})
        self.assertEqual({w["initial"]: (w["name"], w["count"]) for w in r["workers"]},
                         {"KMS": ("홍길동", 1), "HYC": ("", 1), "JJH": ("이서연", 1)})
        y26 = [y for y in r["years"] if y["year"] == 2026][0]
        self.assertEqual(len(y26["months"]), 12)
        self.assertEqual(y26["months"][7], {"month": 8, "bytes": 12 * MB, "count": 1})
        self.assertEqual(y26["months"][1]["count"], 1)
        self.assertEqual([y["year"] for y in r["years"]], [2026, 2025, 2024])

    def test_unsorted_last(self):
        write_tree(self.root, {"260101/a.mov": (500 * MB, ts(2026, 1, 1))})
        idx = scanner.scan_subtree(self.root, self.drive, {})
        r = projects.group_projects(projects.detect_projects(idx, self.settings), "client")
        self.assertEqual(r["groups"][-1]["title"], projects.UNSORTED)
        self.assertEqual(r["clients"][-1]["name"], projects.UNSORTED)

    def test_by_time_and_month(self):
        r = projects.group_projects(self.ps, "time")
        self.assertEqual([g["key"] for g in r["groups"]],
                         ["2026-08", "2026-02", "2025-11", "2025-10", "2025-07", "2025-06",
                          "2025-05", "2025-03", "2024-12"])
        self.assertEqual(r["groups"][0]["title"], "2026년 8월")
        may = [g for g in r["groups"] if g["key"] == "2025-05"][0]
        self.assertEqual([p["name"] for p in may["projects"]], ["b", "2025_다온전자_SNS"])
        r = projects.group_projects(self.ps, "month")
        self.assertEqual(r["year"], 2026)
        self.assertEqual([g["key"] for g in r["groups"]], ["2026-08", "2026-02"])
        r = projects.group_projects(self.ps, "month", year="2025")
        self.assertEqual(r["total"], 7)
        self.assertEqual(len(r["years"]), 3)                     # 칩은 연도 필터와 무관

    def test_filters_and_sort(self):
        r = projects.group_projects(self.ps, "time", q="진정")
        self.assertEqual(r["total"], 2)
        r = projects.group_projects(self.ps, "time", q="한결 편집")
        self.assertEqual(r["total"], 1)
        r = projects.group_projects(self.ps, "client", worker="jjh")
        self.assertEqual((r["total"], r["groups"][0]["title"]), (1, "소담코스"))
        self.assertEqual(len(r["workers"]), 3)                   # 칩은 그대로
        r = projects.group_projects(self.ps, "client", client=" 한결 증권 ")
        self.assertEqual(r["total"], 3)
        r = projects.group_projects(self.ps, "client", client="한결증권", sort="size")
        self.assertEqual([p["size"] for p in r["groups"][0]["projects"]],
                         [96 * MB, 2 * MB, 2 * MB])
        r = projects.group_projects(self.ps, "client", client="소담코스", sort="name")
        self.assertEqual([p["title"] for p in r["groups"][0]["projects"]],
                         ["녹차", "녹차 진정크림 배너결합", "진정크림"])
        r = projects.group_projects(self.ps, "client", drive="E")
        self.assertEqual((r["total"], r["groups"], r["clients"]), (0, [], []))
        r = projects.group_projects(self.ps, "nope", sort="nope", year="x")
        self.assertEqual(r["total"], 10)


class TestVideos(Base):
    def test_project_videos(self):
        write_tree(self.root, {
            "251023_한결증권_브랜드필름_편집_HYC/render/작은.mp4": (10, ts(2025, 10, 21)),
            "251023_한결증권_브랜드필름_편집_HYC/Media Cache Files/x.mp4": (10, ts(2025, 10, 30)),
            "251023_한결증권_브랜드필름_편집_HYC/render/still.png": (10, ts(2025, 10, 30)),
        })
        proj = self.P("251023_한결증권_브랜드필름_편집_HYC")
        v = projects.project_videos(proj, root=self.root)
        self.assertEqual([(x["name"], x["cat"]) for x in v],
                         [("BF_v01.mp4", C.RENDER), ("작은.mp4", C.RENDER),
                          ("C0001.MP4", C.SOURCE)])
        self.assertEqual(v[0], {"path": os.path.join(proj, "render", "BF_v01.mp4"),
                                "name": "BF_v01.mp4", "size": 3 * MB,
                                "mtime": ts(2025, 10, 22), "cat": C.RENDER})
        self.assertEqual(len(projects.project_videos(proj, limit=1, root=self.root)), 1)
        self.assertEqual(len(projects.project_videos(proj)), 3)        # root 없이도 동작
        self.assertEqual(projects.project_videos(self.P("없음")), [])
        with mock.patch.object(projects, "VIDEO_LIST_MAX_ENTRIES", 3):
            self.assertLessEqual(len(projects.project_videos(proj, root=self.root)), 3)
        p = projects.preview_of(self.idx, model.find_dir(self.idx, proj))
        self.assertEqual(p["name"], "BF_v01.mp4")


if __name__ == "__main__":
    unittest.main()
