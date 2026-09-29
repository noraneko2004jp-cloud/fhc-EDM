"""原本を守るための確認：ファイルサーバーに書き込む・消す・名前を変える処理がコードに入っていないこと。

HOUSE設計 の共有は匿名アクセスのため、ファイルサーバー側では書き込みを止められない。
その代わり、このシステムの側で「読むことしかしない」ことをテストで保証する。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = list((ROOT / "worker" / "dwgworker").glob("*.py")) + [ROOT / "server" / "drawings" / "sources.py"]
FORBIDDEN = [
    r"\b(?:smb|smbclient|smb_client)\.(remove|unlink|rmdir|removedirs|rename|replace|makedirs|mkdir|symlink|link|truncate|utime|copyfile|copy2?|move|setxattr|removexattr)\(",
    r"open_file\([^)]*mode\s*=\s*[\"'][^\"']*[wax+]",
]


class ReadOnlyTests(unittest.TestCase):
    def test_no_write_operations_on_file_server(self):
        for f in FILES:
            src = f.read_text(encoding="utf-8")
            for pat in FORBIDDEN:
                self.assertIsNone(re.search(pat, src), f"{f.name} に書き込み系の操作があります: {pat}")

    def test_smb_files_opened_read_only(self):
        for f in FILES:
            for line in f.read_text(encoding="utf-8").splitlines():
                if "open_file(" in line:
                    self.assertIn('mode="rb"', line, f"{f.name}: open_file は mode=\"rb\" で開くこと")


if __name__ == "__main__":
    unittest.main()
