"""使い方（Mac mini）:
  python -m dwgworker check        設定とサーバー接続を確認
  python -m dwgworker scan         1回だけ巡回して、新規・変更ファイルをサーバーに登録
  python -m dwgworker work --once  たまっているジョブを処理して終わる
  python -m dwgworker run          常駐：一定間隔で巡回しつつ、ジョブを処理し続ける
  python -m dwgworker parse FILE   1ファイルを解析して結果を表示（サーバー不要・動作確認用）
  python -m dwgworker probe [smb://サーバー/共有] [--minutes 10]
                                   ファイルサーバーに接続し、直下のフォルダごとの件数を表示（読み取りのみ）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor

from . import source
from .client import Api
from .config import CONFIG
from .process import process

log = logging.getLogger("dwgworker")
BATCH = 500


def do_scan(api: Api):
    started = time.time()
    buf, total, complete = [], {"created": 0, "changed": 0, "unchanged": 0, "skipped": 0}, False
    try:
        for rel, size, mtime in source.walk():
            buf.append({"path": rel, "size": size, "mtime": mtime})
            if len(buf) >= BATCH:
                for k, v in api.scan(buf).items():
                    total[k] += v
                buf = []
        if buf:
            for k, v in api.scan(buf).items():
                total[k] += v
        complete = True
    finally:
        fin = api.scan_finish(started, complete)
    log.info("巡回 %s：新規 %d・変更 %d・変化なし %d・見つからない %d（%.0f秒）", "完了" if complete else "中断",
             total["created"], total["changed"], total["unchanged"], fin.get("missing", 0), time.time() - started)
    return total


def do_work(api: Api, pool: ProcessPoolExecutor, once=False, deadline=None):
    done = 0
    while True:
        jobs = api.claim(CONFIG.concurrency * 2)
        if not jobs:
            return done
        for out in pool.map(process, jobs):
            if out["ok"]:
                api.result(out["job_id"], out["result"], out["thumb"])
                done += 1
            else:
                log.warning("ジョブ %s 失敗: %s", out["job_id"], out["error"].splitlines()[0])
                api.fail(out["job_id"], out["error"], out["permanent"])
        log.info("解析 %d 件", done)
        if deadline and time.time() > deadline:
            return done


def probe(root, seconds=120):
    """マウントせずに SMB で直接つなぎ、直下のフォルダごとに図面らしいファイルの件数を数える（読み取りのみ）。"""
    import getpass
    from collections import Counter
    from pathlib import PurePosixPath

    if not root.startswith("smb://"):
        print("smb://サーバー/共有名 の形で指定してください"); return 1
    user = CONFIG.smb_user or input("ファイルサーバーのユーザー名（ドメインがあれば DOMAIN\\名前）: ")
    pw = CONFIG.smb_password or getpass.getpass("パスワード（表示されません）: ")
    CONFIG.smb_user, CONFIG.smb_password, CONFIG.source_root = user, pw, root
    smb = source._smb_login(root)
    top = source._unc(root)
    exts = ("dxf", "dwg", "jww", "jwc", "pdf", "tif", "tiff")
    folders = sorted((e for e in smb.scandir(top) if e.is_dir()), key=lambda e: e.name)
    t0 = time.time()
    total = Counter()
    budget = seconds / max(len(folders), 1)
    print(f"直下のフォルダ {len(folders)} 個を、1フォルダあたり最大 {budget:.0f} 秒ずつ数えます")
    for f in folders:
        t, n, stack, size = time.time(), Counter(), [f.path], 0
        while stack and time.time() - t < budget:
            for e in smb.scandir(stack.pop()):
                if e.is_dir():
                    stack.append(e.path)
                else:
                    ext = PurePosixPath(e.name).suffix.lower()[1:]
                    if ext in exts:
                        n[ext] += 1
                        size += e.stat().st_size
                    else:
                        n["その他"] += 1
        total.update(n)
        detail = "  ".join(f"{k.upper() if k != 'その他' else k} {v}" for k, v in n.most_common())
        print(f"・{f.name}：{detail or '（なし）'}  計 {size / 1e9:.1f}GB{'（途中まで）' if stack else ''}")
    print(f"== 合計（{time.time() - t0:.0f}秒）==", "  ".join(f"{k.upper() if k != 'その他' else k} {v}" for k, v in total.most_common()))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dwgworker")
    ap.add_argument("cmd", choices=["check", "scan", "work", "run", "parse", "probe"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--minutes", type=float, default=2, help="probe で数える時間（分）")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if a.cmd == "parse":
        from pathlib import Path
        from . import parse_dxf, parse_pdf
        p = Path(a.file)
        res, thumb = (parse_dxf if p.suffix.lower() == ".dxf" else parse_pdf).parse(p.read_bytes(), p.name)
        res.pop("items", None)
        for pg in res["pages"]:
            pg["text"] = pg["text"][:300]
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        print(f"サムネイル: {len(thumb) if thumb else 0} bytes")
        return 0

    if a.cmd == "probe":
        logging.getLogger().setLevel(logging.ERROR)  # 接続途中の細かい記録を出さない（入力欄が隠れないように）
        return probe(a.file or CONFIG.source_root, seconds=a.minutes * 60)

    api = Api()
    if a.cmd == "check":
        print("サーバー:", CONFIG.server_url, api.health())
        n = sum(1 for _ in zip(range(20), source.walk()))
        print("図面の置き場所:", CONFIG.source_root, f"（先頭 {n} 件を確認）")
        return 0
    if a.cmd == "scan":
        do_scan(api)
        return 0
    with ProcessPoolExecutor(max_workers=CONFIG.concurrency) as pool:
        if a.cmd == "work":
            do_work(api, pool, once=True)
            return 0
        next_scan = 0.0
        while True:  # run
            try:
                if time.time() >= next_scan:
                    do_scan(api)
                    next_scan = time.time() + CONFIG.scan_interval_min * 60
                if do_work(api, pool, deadline=next_scan) == 0:
                    time.sleep(15)
            except Exception as e:  # サーバーやファイルサーバーが一時的に落ちても止まらない
                log.error("エラー: %s（60秒後に再試行）", e)
                time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
