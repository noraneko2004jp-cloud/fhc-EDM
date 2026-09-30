import json
import tempfile
import time

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from .models import AuditLog, DictionaryEntry, Drawing, Job, SourceFile

TOKEN = "t0ken"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def post_json(client, url, data, **extra):
    return client.post(url, json.dumps(data), content_type="application/json", **{**AUTH, **extra})


@override_settings(WORKER_TOKEN=TOKEN, WORKER_ALLOWED_IPS=[], ALLOWED_HOSTS=["testserver"])
class WorkerApiTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.enterContext(override_settings(MEDIA_ROOT=self.media.name))

    def tearDown(self):
        self.media.cleanup()

    def scan(self, files):
        return post_json(self.client, "/api/internal/scan", {"files": files}).json()

    def test_token_required(self):
        self.assertEqual(self.client.get("/api/internal/health").status_code, 401)
        self.assertEqual(self.client.get("/api/internal/health", HTTP_AUTHORIZATION="Bearer wrong").status_code, 401)
        self.assertEqual(self.client.get("/api/internal/health", **AUTH).status_code, 200)

    @override_settings(WORKER_ALLOWED_IPS=["10.0.0.9"])
    def test_ip_restriction(self):
        self.assertEqual(self.client.get("/api/internal/health", **AUTH).status_code, 403)

    def test_scan_creates_jobs_only_for_new_or_changed(self):
        t = time.time()
        r = self.scan([{"path": "a/UH-1.dxf", "size": 10, "mtime": t}, {"path": "a/x.txt", "size": 1, "mtime": t}])
        self.assertEqual((r["created"], r["skipped"]), (1, 1))
        self.assertEqual(self.scan([{"path": "a/UH-1.dxf", "size": 10, "mtime": t}])["unchanged"], 1)
        self.assertEqual(Job.objects.count(), 1)  # 変化なし → 追加のジョブなし
        self.assertEqual(self.scan([{"path": "a/UH-1.dxf", "size": 11, "mtime": t}])["changed"], 1)
        self.assertEqual(Job.objects.count(), 1)  # まだ待ち行列にあるので重複させない

    def test_full_job_cycle_and_search_text(self):
        self.scan([{"path": "design/UH-3600-01_A.dxf", "size": 10, "mtime": time.time()}])
        jobs = post_json(self.client, "/api/internal/jobs/claim", {"worker": "mac", "limit": 5}).json()["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(post_json(self.client, "/api/internal/jobs/claim", {"limit": 5}).json()["jobs"], [])
        data = {"sha256": "ab" * 32,
                "drawing": {"drawing_no": "UH-3600-01", "revision": "A", "title": "ユニットハウス トイレ付", "source": "attrib", "confidence": 0.97},
                "pages": [{"page_no": 1, "text": "注記"}],
                "bom": [{"part_no": "TL-01", "name": "洋式トイレユニット", "qty": "1"}]}
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            f.write(PNG)
            f.seek(0)
            r = self.client.post(f"/api/internal/jobs/{jobs[0]['job_id']}/result", {"data": json.dumps(data), "thumbnail": f}, **AUTH)
        self.assertEqual(r.status_code, 200, r.content)
        d = Drawing.objects.get()
        self.assertEqual(d.bom_items.get().part_no, "TL-01")
        self.assertIn("TL-01", d.search_text)
        self.assertIn("ユニットハウス", d.search_text)
        self.assertTrue(d.thumbnail)
        self.assertEqual(d.file.status, SourceFile.Status.DONE)

    def test_drawing_set_pages_become_drawings(self):
        self.scan([{"path": "set/20-032 CAK-A 図面一式.pdf", "size": 10, "mtime": time.time()}])
        job = post_json(self.client, "/api/internal/jobs/claim", {"limit": 1}).json()["jobs"][0]
        data = {"sha256": "cd" * 32, "drawings": [
            {"page_no": n, "drawing": {"drawing_no": no, "title": t, "source": "ocr", "attributes": {"model": "CAK-A", "pages": 2}},
             "pages": [{"page_no": n, "text": f"注記 {no}"}], "bom": []}
            for n, no, t in [(1, "HDBY003920", "ハイキパネル/FP"), (2, "HDBY003930", "フレーム")]]}
        with tempfile.NamedTemporaryFile(suffix=".png") as a, tempfile.NamedTemporaryFile(suffix=".png") as b:
            a.write(PNG); a.seek(0); b.write(PNG); b.seek(0)
            r = self.client.post(f"/api/internal/jobs/{job['job_id']}/result",
                                 {"data": json.dumps(data), "thumb_1": a, "thumb_2": b}, **AUTH)
        self.assertEqual(r.status_code, 200, r.content)
        ds = Drawing.objects.order_by("page_no")
        self.assertEqual([(d.page_no, d.drawing_no) for d in ds], [(1, "HDBY003920"), (2, "HDBY003930")])
        self.assertTrue(all(d.thumbnail for d in ds))
        self.assertIn("CAK-A", ds[0].search_text)
        # 再解析で 1 ページになったら 2 ページ目の図面は消える
        SourceFile.objects.update(size=11)
        self.scan([{"path": "set/20-032 CAK-A 図面一式.pdf", "size": 12, "mtime": time.time()}])
        job = post_json(self.client, "/api/internal/jobs/claim", {"limit": 1}).json()["jobs"][0]
        data["drawings"] = data["drawings"][:1]
        self.client.post(f"/api/internal/jobs/{job['job_id']}/result", {"data": json.dumps(data)}, **AUTH)
        self.assertEqual(Drawing.objects.count(), 1)

    def test_nul_characters_are_removed(self):
        self.scan([{"path": "x/NUL.pdf", "size": 1, "mtime": time.time()}])
        job = post_json(self.client, "/api/internal/jobs/claim", {"limit": 1}).json()["jobs"][0]
        data = {"drawings": [{"page_no": 1, "drawing": {"drawing_no": "HB\u00000001", "title": "a\u0000b",
                                                         "attributes": {"k\u0000": "v\u0000"}},
                              "pages": [{"page_no": 1, "text": "注記\u0000本文"}], "bom": []}]}
        r = self.client.post(f"/api/internal/jobs/{job['job_id']}/result", {"data": json.dumps(data)}, **AUTH)
        self.assertEqual(r.status_code, 200, r.content)
        d = Drawing.objects.get()
        self.assertEqual((d.drawing_no, d.title), ("HB0001", "ab"))
        self.assertEqual(d.pages.get().text, "注記本文")

    def test_fail_retries_then_gives_up(self):
        self.scan([{"path": "x/UH-2.pdf", "size": 1, "mtime": time.time()}])
        for i in range(3):
            job = post_json(self.client, "/api/internal/jobs/claim", {"limit": 1}).json()["jobs"][0]
            retry = post_json(self.client, f"/api/internal/jobs/{job['job_id']}/fail", {"error": "boom"}).json()["retry"]
        self.assertFalse(retry)
        self.assertEqual(SourceFile.objects.get().status, SourceFile.Status.ERROR)

    def test_missing_only_after_complete_scan(self):
        t = time.time()
        self.scan([{"path": "a/1.dxf", "size": 1, "mtime": t}, {"path": "a/2.dxf", "size": 1, "mtime": t}])
        started = time.time() + 1
        SourceFile.objects.filter(path="a/1.dxf").update(last_seen=None)
        SourceFile.objects.filter(path="a/2.dxf").update(last_seen="2000-01-01T00:00:00Z")
        r = post_json(self.client, "/api/internal/scan/finish", {"started_at": started, "complete": False}).json()
        self.assertEqual(r["missing"], 0)
        r = post_json(self.client, "/api/internal/scan/finish", {"started_at": started, "complete": True}).json()
        self.assertEqual(r["missing"], 1)


@override_settings(ALLOWED_HOSTS=["testserver"])
class SearchViewTests(TestCase):
    def setUp(self):
        from datetime import datetime, timezone
        self.user = User.objects.create_user("u", password="pw-12345678")
        for i, (no, title, part) in enumerate([("UH-3600", "事務所", "WP-900"), ("UH-3600-01", "トイレ付事務所", "TL-01"), ("UH-3602", "コーナー柱", "UC-2400")]):
            f = SourceFile.objects.create(path=f"d/{no}.dxf", kind="dxf", size=1, mtime=datetime.now(timezone.utc))
            d = Drawing.objects.create(file=f, drawing_no=no, title=title)
            d.bom_items.create(row=1, part_no=part, name=title)
            from .text import build_search_text
            d.search_text = build_search_text(no, title, part)
            d.save()
        for t in ["トイレ", "便所", "TOILET"]:
            DictionaryEntry.objects.create(term=t, canonical="トイレ", approved=True)
        DictionaryEntry.objects.create(term="WC", canonical="トイレ", approved=False)

    def test_login_required(self):
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_search_normalizes_and_expands(self):
        self.client.login(username="u", password="pw-12345678")
        r = self.client.get("/", {"q": "ｕｈ－３６００"})
        self.assertEqual(r.context["total"], 2)
        r = self.client.get("/", {"q": "便所"})
        self.assertEqual(r.context["total"], 1)
        self.assertIn("トイレ", r.context["terms"])
        self.assertNotIn("WC", r.context["terms"])  # 未承認の語は使わない
        r = self.client.get("/", {"q": "UH-3600 トイレ"})  # AND
        self.assertEqual(r.context["total"], 1)

    def test_detail_writes_audit_log(self):
        self.client.login(username="u", password="pw-12345678")
        d = Drawing.objects.get(drawing_no="UH-3602")
        self.assertEqual(self.client.get(f"/d/{d.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/d/{d.pk}/bom.xlsx").status_code, 200)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_back_keeps_page_and_prev_next(self):
        from datetime import datetime, timezone
        from .text import build_search_text
        for n in range(120):
            f = SourceFile.objects.create(path=f"p/ZZ{n:04d}.pdf", kind="pdf", size=1, mtime=datetime.now(timezone.utc))
            Drawing.objects.create(file=f, drawing_no=f"ZZ{n:04d}", search_text=build_search_text(f"ZZ{n:04d}"))
        self.client.login(username="u", password="pw-12345678")
        r = self.client.get("/", {"q": "ZZ", "kind": "pdf", "page": 2})
        it = r.context["items"][0]
        d = it["d"]
        self.assertEqual((it["i"], d.drawing_no), (50, "ZZ0050"))
        self.assertIn("i=50", it["link"])
        r = self.client.get(f"/d/{d.pk}/{it['link']}")
        self.assertIn("page=2", r.context["back"])
        self.assertIn("kind=pdf", r.context["back"])
        self.assertTrue(r.context["back"].endswith(f"#f{d.file_id}"))
        nav = r.context["nav"]
        self.assertEqual((nav["pos"], nav["total"]), (51, 120))
        self.assertEqual((nav["prev"][0].drawing_no, nav["next"][0].drawing_no), ("ZZ0049", "ZZ0051"))
        self.assertIn("i=49", nav["prev"][1])
        first = Drawing.objects.get(drawing_no="ZZ0000")
        r = self.client.get(f"/d/{first.pk}/", {"q": "ZZ", "i": 0})
        self.assertNotIn("prev", r.context["nav"])
        r = self.client.get(f"/d/{first.pk}/", {"q": "ZZ", "p": 3})
        self.assertEqual(r.context["nav"], {})
        self.assertIn("page=3", r.context["back"])
        r = self.client.get("/", {"q": "ZZ"})
        self.assertEqual(r.context["window"], [1, 2, 3])


class ClassifyTests(TestCase):
    def test_series(self):
        from .classify import series_of
        self.assertEqual(series_of("HB0011XXXX"), ("HB", "HB0011"))
        self.assertEqual(series_of("HDBY003920"), ("HDBY", "HDBY0039"))
        self.assertEqual(series_of("HDBY003930"), ("HDBY", "HDBY0039"))
        self.assertEqual(series_of("UH-3600-01"), ("UH", "UH-3600"))
        self.assertEqual(series_of("UH-3600"), ("UH", "UH-3600"))
        self.assertEqual(series_of("1234567890"), ("数字", "123456"))
        self.assertEqual(series_of(""), ("_", "_"))

    def test_model(self):
        from .classify import model_of
        self.assertEqual(model_of("図面/x/20-032 CAK-A 多用途ﾊｳｽ 図面一式.pdf", {"model": "CAK-A"}), ("CAK", "CAK-A"))
        self.assertEqual(model_of("図面/dxfCAK-40A 田の字36R-221003.dxf"), ("CAK", "CAK-40A"))
        self.assertEqual(model_of("図面 DXF・DWG・JW・PDF/JH/JH 標準/HB0011XXXX 差替3.pdf"), ("JH", "JH"))
        self.assertEqual(model_of("図面/CAG-12/部品/UH-3600-01.dxf"), ("CAG", "CAG-12"))
        self.assertEqual(model_of("図面/部品/UH-3600-01.dxf"), ("_", "_"))  # 図番は型式にしない
        self.assertEqual(model_of("PDF/DXF-1/a.pdf"), ("_", "_"))
        # OCR で空白だけ・文字以外が入っていても止まらない
        for bad in (" ", "\u3000", 123, ["CAK"], None, ""):
            self.assertEqual(model_of("a/b.pdf", {"model": bad}), ("_", "_"))


@override_settings(ALLOWED_HOSTS=["testserver"])
class BrowseViewTests(TestCase):
    def setUp(self):
        from datetime import datetime, timezone
        from . import classify
        from .text import build_search_text
        User.objects.create_user("u", password="pw-12345678")
        self.client.login(username="u", password="pw-12345678")
        now = datetime.now(timezone.utc)
        rows = [("図面/CAK/CAK-A/20-032 CAK-A 図面一式.pdf", [(1, "HDBY003920"), (2, "HDBY003930"), (3, "HDBY004010")]),
                ("図面/CAK/dxfCAK-40A 田の字36R-221003.dxf", [(1, "")]),
                ("図面/JH/HB0011XXXX差替3 スペーサー.pdf", [(1, "HB0011XXXX")]),
                ("資料/申請書.pdf", [(1, "")])]
        for path, pages in rows:
            f = SourceFile.objects.create(path=path, kind=path[-3:], size=1, mtime=now)
            for page_no, no in pages:
                d = Drawing(file=f, page_no=page_no, drawing_no=no, attributes={"pages": len(pages)},
                            search_text=build_search_text(no, path))
                classify.apply(d)
                d.save()

    def test_list_collapses_drawing_sets(self):
        r = self.client.get("/", {"v": "list"})
        self.assertEqual(r.context["total"], 4)  # 図面一式の 3 枚は 1 件
        set_item = next(it for it in r.context["items"] if it["size"] == 3)
        self.assertEqual(set_item["d"].page_no, 1)
        self.assertContains(r, "一式 3枚")
        self.assertEqual(r.context["items"][-1]["d"].drawing_no, "")  # 図番のないものは後ろ
        # 検索で 2 ページ目だけ一致したら、代表は 2 ページ目
        r = self.client.get("/", {"q": "HDBY003930"})
        self.assertEqual([it["d"].page_no for it in r.context["items"]], [2])

    def test_folder_tree(self):
        r = self.client.get("/", {"v": "folder"})
        self.assertEqual([(n["label"], n["n"]) for n in r.context["nodes"]], [("図面", 5), ("資料", 1)])
        r = self.client.get("/", {"v": "folder", "f": "図面/CAK"})
        labels = [(n["label"], n["depth"], n["current"]) for n in r.context["nodes"]]
        self.assertIn(("CAK", 1, True), labels)
        self.assertIn(("CAK-A", 2, False), labels)
        self.assertIn(("JH", 1, False), labels)
        self.assertIn(("資料", 0, False), labels)
        self.assertEqual(r.context["total"], 2)  # CAK 以下のファイル
        self.assertEqual([c[0] for c in r.context["crumbs"]], ["図面", "CAK"])
        # 見せ方を覚える
        r = self.client.get("/")
        self.assertEqual(r.context["st"]["v"], "folder")

    def test_model_and_series(self):
        r = self.client.get("/", {"v": "model"})
        self.assertEqual({n["label"]: n["n"] for n in r.context["nodes"]}, {"CAK": 4, "JH": 1, "（型式なし）": 1})
        r = self.client.get("/", {"v": "model", "m": "CAK"})
        self.assertIn("CAK-40A", [n["label"] for n in r.context["nodes"]])
        r = self.client.get("/", {"v": "model", "m": "CAK/CAK-40A"})
        self.assertEqual(r.context["total"], 1)
        labels = [n["label"] for n in r.context["nodes"]]
        self.assertIn("CAK-A", labels)  # 選んだ型式の兄弟も見える
        self.assertIn("JH", labels)
        r = self.client.get("/", {"v": "series", "s": "HDBY/HDBY0039"})
        self.assertEqual(r.context["total"], 1)
        self.assertEqual(r.context["drawing_total"], 2)

    def test_detail_nav_in_folder_view(self):
        r = self.client.get("/", {"v": "folder", "f": "図面"})
        it = r.context["items"][0]
        r = self.client.get(f"/d/{it['d'].pk}/{it['link']}")
        self.assertIn("v=folder", r.context["back"])
        self.assertIn("f=", r.context["back"])
        self.assertEqual(r.context["nav"]["total"], 3)
        self.assertTrue(r.context["folder_link"])
