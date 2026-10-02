"""常駐（run）の起動処理が例外なく巡回まで進むこと。2026-09-29 に Path の扱いの誤りで起動直後に落ちたため。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dwgworker.__main__ as m


class Stop(Exception):
    pass


class RunStartupTests(unittest.TestCase):
    def test_run_reaches_scan(self):
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.object(m, "Api"), \
                mock.patch.object(m, "do_scan", side_effect=Stop), \
                mock.patch.object(Path, "home", return_value=Path(home)), \
                mock.patch.object(m.log, "error", side_effect=Stop):
            with self.assertRaises(Stop):
                m.main(["run"])


if __name__ == "__main__":
    unittest.main()


class ScanSpeedTests(unittest.TestCase):
    def test_walk_uses_directory_listing_not_stat(self):
        """巡回は、フォルダの一覧に入っているサイズ・更新日時を使い、ファイルごとの stat() はしない。"""
        import datetime as dt
        from collections import namedtuple
        from unittest import mock
        from dwgworker import source

        Info = namedtuple("Info", "end_of_file last_write_time")
        when = dt.datetime(2026, 10, 1, 12, 0, 0, 123456, tzinfo=dt.timezone.utc)

        class Entry:
            def __init__(self, name, is_dir=False, size=0):
                self.name, self._dir, self.smb_info = name, is_dir, Info(size, when)
            def is_dir(self): return self._dir
            def is_symlink(self): return False
            def stat(self): raise AssertionError("stat() を呼んではいけない")

        tree = {"": [Entry("図面", True), Entry("memo.txt", size=3)],
                "図面": [Entry("HB0011XXXX.dxf", size=1234), Entry("a.PDF", size=5)]}
        smb = mock.Mock()
        smb.scandir.side_effect = lambda unc: tree[unc.split("share")[-1].strip("\\").replace("\\", "/")]
        with mock.patch.object(source, "_smb_login", return_value=smb):
            got = sorted(source.walk("smb://sv/share", use_include=False))
        self.assertEqual(got, [("図面/HB0011XXXX.dxf", 1234, when.timestamp()), ("図面/a.PDF", 5, when.timestamp())])

    def test_falls_back_to_stat_without_listing_info(self):
        from unittest import mock
        from dwgworker import source
        e = mock.Mock(spec=["stat", "is_symlink"])
        e.stat.return_value = mock.Mock(st_size=7, st_mtime=1.5)
        self.assertEqual(source._size_mtime(e), (7, 1.5))

    def test_scan_uses_slow_walk_for_old_server(self):
        """サーバーが更新日時の秒未満の違いを吸収できない（古い）ときは、これまでどおり stat() で巡回する。"""
        from unittest import mock
        api = mock.Mock()
        api.scan.return_value = {"created": 0, "changed": 0, "unchanged": 0, "skipped": 0}
        api.scan_finish.return_value = {"missing": 0}
        for health, want in (({"ok": True}, False), ({"ok": True, "scan_mtime_tolerant": True}, True)):
            api.health.return_value = health
            with mock.patch.object(m.source, "walk", return_value=iter([])) as walk:
                m.do_scan(api)
            self.assertEqual(walk.call_args.kwargs.get("fast"), want)
