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
