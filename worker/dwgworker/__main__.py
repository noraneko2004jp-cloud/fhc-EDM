"""使い方（Mac mini）:
  python -m dwgworker check        設定とサーバー接続を確認
  python -m dwgworker scan         1回だけ巡回して、新規・変更ファイルをサーバーに登録
  python -m dwgworker work --once  たまっているジョブを処理して終わる
  python -m dwgworker run          常駐：一定間隔で巡回しつつ、ジョブを処理し続ける
  python -m dwgworker parse FILE   1ファイルを解析して結果を表示（サーバー不要・動作確認用。スキャンPDFはOCRも行う）
  python -m dwgworker probe [smb://サーバー/共有] [--minutes 10]
                                   ファイルサーバーに接続し、直下のフォルダごとの件数を表示（読み取りのみ）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
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
        seen = 0
        for rel, size, mtime in source.walk():
            buf.append({"path": rel, "size": size, "mtime": mtime})
            seen += 1
            if len(buf) >= BATCH:
                for k, v in api.scan(buf).items():
                    total[k] += v
                buf = []
                if seen % 2000 == 0:
                    log.info("巡回中：%d 件（新規 %d・変更 %d）%s", seen, total["created"], total["changed"], rel.rsplit("/", 1)[0])
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
                api.result(out["job_id"], out["result"], out["thumbs"])
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
    user = CONFIG.smb_user or input("ファイルサーバーのユーザー名（ドメインがあれば DOMAIN\\名前、匿名接続なら guest）: ")
    pw = CONFIG.smb_password or ("" if user.lower() in ("guest", "anonymous") else getpass.getpass("パスワード（表示されません）: "))
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
    # SMB ライブラリはファイル 1 件ごとに INFO を出してログが膨らむため、警告以上だけにする
    for name in ("smbprotocol", "smbclient", "spnego"):
        logging.getLogger(name).setLevel(logging.WARNING)

    if a.cmd == "parse":
        from pathlib import Path
        from . import parse_dxf, parse_pdf
        p = Path(a.file)
        res, thumbs = (parse_dxf if p.suffix.lower() == ".dxf" else parse_pdf).parse(p.read_bytes(), p.name)
        res.pop("items", None)
        for dr in res["drawings"]:
            for pg in dr["pages"]:
                pg["text"] = pg["text"][:1500 if pg["page_no"] == dr["page_no"] else 200]
            dr["drawing"]["attributes"].pop("attribs", None)
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        print(f"図面 {len(res['drawings'])} 件 / サムネイル {len(thumbs)} 枚")
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
        # 前回の巡回が終わった時刻を覚えておき、再起動のたびに最初から巡回し直さない
        stamp = Path.home() / ".dwgfind-last-scan"
        try:
            next_scan = float(stamp.read_text()) + CONFIG.scan_interval_min * 60
        except (OSError, ValueError):
            next_scan = 0.0
        if next_scan > time.time():
            log.info("前回の巡回から %d 分以内のため、解析の続きから始めます", CONFIG.scan_interval_min)
        while True:  # run
            try:
                if time.time() >= next_scan:
                    # 巡回が途中で失敗しても、次の巡回は間隔をあけてから。その間にたまった解析を進める
                    next_scan = time.time() + CONFIG.scan_interval_min * 60
                    do_scan(api)
                    try:
                        stamp.write_text(str(time.time()))
                    except OSError:
                        pass
                if do_work(api, pool, deadline=next_scan) == 0:
                    time.sleep(15)
            except Exception as e:  # サーバーやファイルサーバーが一時的に落ちても止まらない
                log.error("エラー: %s（60秒後に再試行）", e)
                time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
