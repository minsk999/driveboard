"""core/brc.py ↔ tests/brc_reference.js 차등 시험 + 날짜 헬퍼 손 벡터."""

import itertools
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import brc  # noqa: E402

REF = os.path.join(HERE, "brc_reference.js")

_NODE_SCRIPT = r"""
const BRC = require(process.argv[1]);
let data = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', c => data += c);
process.stdin.on('end', () => {
  const req = JSON.parse(data);
  const out = {parse: [], page: [], worker: [], jobKind: [], workType: [], extra: []};
  for (const n of req.names) {
    out.parse.push(BRC.parse(n));
    out.page.push(BRC.applyPage(n, '3'));
    out.worker.push(BRC.applyWorker(n, 'PNJ'));
    out.jobKind.push(BRC.applyJobKind(n, '@BC'));
    out.workType.push([BRC.applyWorkType(n, 'retuch1'), BRC.applyWorkType(n, 'xian')]);
  }
  for (const w of req.extra) BRC.WORKER.push(w);   // closure array: extends the list in place
  for (const n of req.names) out.extra.push(BRC.parse(n));
  process.stdout.write(JSON.stringify(out));
});
"""

EXTRA = ["LSH", "CHOI", "mjk"]


def names():
    base = [
        "260803_소담코스_녹차_진정크림_배너결합_KMS",
        "251023_한결증권_브랜드필름_편집_HYC",
        "260115_바른기술_인터뷰_JJH",
        "소담코스_진정크림", "2026-08_소담코스_런칭", "20260803_소담코스", "2025-11_한빛은행_런칭",
        "260803_소담코스_녹차_3p_진정크림_1080x1080_KMS_@BC_xian",
        "260803_소담코스_녹차_12p_배너_1920X1080_PNJ_@MB_retuch3",
        "260803_소담코스_녹차_진정크림_1200x628_kms_@SZ_vari",
        "260803_소담코스_녹차_진정크림_ABC_@ED",
        "260803_소담코스_녹차_진정크림_abc_@ED",
        "소담코스_녹차_진정크림_ABCD",
        "소담코스_녹차_ABCD", "소담코스_ABCD", "KMS_소담코스_녹차", "소담코스_KMS",
        "소담코스_녹차_KMS 2", "소담코스_녹차_KMS  12", "소담코스_녹차_KMS 2a",
        "ver_2", "소담코스_ver_2_KMS", "소담코스_cut_03_수정_HYC", "shot_010_v_2",
        "260803_2_소담코스", "1080x1080_3", "3p_2", "@BC_2", "xian_2", "retuch1_4",
        "", "_", "__", "___KMS", "KMS", "kms", "@BC", "xian", "3p", "1080x1080",
        "260803", "2608031", "26080", "a_b_c_d_e_f_g_h_i", "A_B_C_D_E_F",
        "소담코스_녹차_진정크림_SF", "소담코스_녹차_sf", "소담코스_녹차_retuch", "소담코스_녹차_retuchA",
        "소담코스_녹차_retuch12_KMS", "소담코스__녹차___진정크림_", "  소담코스_녹차  ",
        "소담코스_녹차_진정크림 ", "소담코스_녹차_진정크림\t3", "소담코스_녹차_진정크림 3",
        "소담코스_녹차_진정크림　3", "소담코스_녹차_진정크림﻿3", "소담코스_녹차_진정크림\x1c3",
        "소담코스_녹차_진정크림\n3", "소담코스_녹차_진정크림\n", "260803_소담코스\n",
        "소담코스_녹차_0p_x", "소담코스_녹차_p_x", "소담코스_녹차_01p_x", "소담코스_녹차_12P_x",
        "소담코스_녹차_100x100", "소담코스_녹차_99999x1080", "소담코스_녹차_１０８０x1080",
        "소담코스_녹차_１２p", "２６０８０３_소담코스", "Kiwoom_BrandFilm_Edit_HYC",
        "Kiwoom_Brand Film_Edit_hyc", "KIWOOM_BRAND_FILM_EDIT", "KIWOOM_BRAND_FILM",
        "Ab_Cd_EF_GH", "ab_cd_ef_GHIJKL", "ab_cd_ef_GHIJK", "ab_cd_ef_G", "ab_cd_ef_G2",
        "ab_@BC", "AB_@BC", "ab_cd_AB_@BC_KMS", "ab_cd_KMS_@BC_HYC", "ab_cd_@MB_@BC",
        "ab_cd_xian_vari", "ab_cd_@XX", "ab_cd_@bc", "ab_cd_@CP_retuch9",
        "260803_소담코스_녹차_진정크림_배너결합_KMS_v2", "260803_소담코스_녹차_진정크림_배너결합_KMS_2",
        "260803_소담코스_녹차_진정크림_배너결합_KMS 3", "260803_소담코스_녹차_1080x1920_1",
        "draft_1_2_3", "a_1", "1_a", "1_2", "a_b_1_c_2_d_3", "소담코스_시즌_2_녹차",
        "x.mp4", "260803_소담코스_final.mp4", "소담코스_녹차.aep", "foo bar_baz qux",
        "소담코스-녹차-진정크림", "소담코스.녹차.진정크림", "(소담코스)_[녹차]",
        "소담코스_녹차_PNJ_KBH", "소담코스_PNJ_KBH", "PNJ_KBH_GJH", "소담코스_녹차_수딩_PNJ_KBH_GJH",
        "소담코스_녹차_GJH_@AV", "소담코스_녹차_gjh_@AV", "소담코스_녹차_Gjh", "소담코스_녹차_JJH_x",
        "소담코스_녹차_LSH", "소담코스_녹차_lsh", "소담코스_LSH", "소담코스_녹차_수딩_CHOI", "소담코스_녹차_MJK",
        "소담코스_녹차_mjk", "LSH_소담코스_녹차", "소담코스_녹차_진정크림_LSH_KMS",
        "1920x1080_1080x1080_소담코스", "소담코스_1920x1080_1080x1080", "소담코스_3p_4p",
        "소담코스_xian_vari_sf", "소담코스_retuch2_retuch5", "ß_ı_ſ", "소담코스_녹차_İ",
        "ÀÉ_ÔÜ_ÑÇ", "Ω_Σ_ΔΛ", "日本_中国_한국",
    ]
    out = list(base)
    clients = ["소담코스", "한결증권", "바른기술", "Kiwoom", "한빛은행"]
    camps = ["녹차", "브랜드필름", "런칭", "Summer"]
    tasks = ["진정크림_배너결합", "편집", "인터뷰", "cut_2"]
    extras = ["", "_3p", "_1080x1080", "_@BC", "_xian", "_retuch2", "_KMS", "_HYC_@MB",
              "_ABC", " 2", "_LSH"]
    for c, ca, t, ex in itertools.islice(itertools.product(clients, camps, tasks, extras),
                                         0, None, 7):
        out.append("260803_%s_%s_%s%s" % (c, ca, t, ex))
    for i, ex in enumerate(extras):
        out.append("%s_%s%s" % (clients[i % 5], camps[i % 4], ex))
        out.append("2026-0%d_%s%s" % (1 + i % 9, clients[i % 5], ex))
    seen, uniq = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def py_side(ns):
    p = brc.parser(EXTRA)
    d = brc.Parser()
    return {
        "parse": [brc.parse(n) for n in ns],
        "page": [d.apply_page(n, "3") for n in ns],
        "worker": [d.apply_worker(n, "PNJ") for n in ns],
        "jobKind": [d.apply_job_kind(n, "@BC") for n in ns],
        "workType": [[d.apply_work_type(n, "retuch1"), d.apply_work_type(n, "xian")]
                     for n in ns],
        "extra": [p.parse(n) for n in ns],
    }


@unittest.skipIf(shutil.which("node") is None, "node 가 없어 차등 시험을 건너뜀")
class TestDifferential(unittest.TestCase):
    def test_against_reference(self):
        ns = names()
        self.assertGreaterEqual(len(ns), 250)
        r = subprocess.run(["node", "-e", _NODE_SCRIPT, REF], input=json.dumps({
            "names": ns, "extra": EXTRA}), capture_output=True, text=True, encoding="utf-8",
            timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        js = json.loads(r.stdout)
        py = py_side(ns)
        for key in ("parse", "page", "worker", "jobKind", "workType", "extra"):
            for n, a, b in zip(ns, py[key], js[key]):
                self.assertEqual(a, b, "%s(%r)" % (key, n))


class TestHand(unittest.TestCase):
    def test_real_example(self):
        self.assertEqual(brc.parse("260803_소담코스_녹차_진정크림_배너결합_KMS"), {
            "client": "소담코스", "campaign": "녹차", "page": "", "task": "진정크림_배너결합",
            "resolution": "", "worker": "KMS", "jobKind": "", "workType": ""})

    def test_extra_worker(self):
        self.assertEqual(brc.parse("소담코스_LSH")["worker"], "")
        self.assertEqual(brc.parse("소담코스_LSH", ["LSH"])["worker"], "LSH")

    def test_parse_date(self):
        V = {
            "260803_소담코스": (2026, 8, 3), "20260803_소담코스": (2026, 8, 3),
            "2026-08-03 소담코스": (2026, 8, 3), "2026.08.03_소담코스": (2026, 8, 3),
            "2026_08_03_소담코스": (2026, 8, 3), "2026-8-3_x": (2026, 8, 3),
            "2026-08_소담코스_런칭": (2026, 8, None), "2026_08_소담코스": (2026, 8, None),
            "2025-11": (2025, 11, None), "251023": (2025, 10, 23), "240229_윤년": (2024, 2, 29),
            "260803소담코스": (2026, 8, 3), "  260803_x": (2026, 8, 3),
            "261303_x": None, "260230_x": None, "230229_x": None, "2026-13_x": None,
            "2026-00_x": None, "20261301_x": None, "2026-02-30_x": None, "000000_x": None,
            "2608031_x": None, "26080_x": None, "260803a_x": None, "260803p": None,
            "소담코스_260803": None, "": None, "x": None, "1899-01-01": None, "2026.08_x": None,
            "18990101_x": None, "2026-08-03-x": (2026, 8, 3), "260803.mp4": (2026, 8, 3),
        }
        for s, want in V.items():
            self.assertEqual(brc.parse_date(s), want, s)


if __name__ == "__main__":
    unittest.main()
