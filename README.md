# DWG-FIND 図面検索システム

金属製ユニットハウスの DXF・PDF 図面を、Windows ファイルサーバーから読み取り専用で巡回・解析し、
図番・品名・部品・派生ハウスから検索、表示、ダウンロード、部品表の Excel 出力を行う社内システム。

- 設計書・プロトタイプ: https://claude.ai/artifact/5fXyyadnhmm3co52BqDzdH
- 作業記録: Notion「DWG-FIND 図面検索システム」

## 構成

| 機器 | 役割 | このリポジトリ |
|---|---|---|
| Ubuntu（Hyper-V, 128.131.250.252） | Django 画面・社内API・PostgreSQL・サムネイル・ダウンロード配信 | `server/`, `docker-compose.yml`, `ops/` |
| Mac mini M4（128.131.250.253） | 巡回・DXF/PDF 解析・OCR・Ollama | `worker/`, `macmini/` |

Mac mini は DB に直接つながず、Ubuntu の社内API（`/api/internal/*`、トークン認証）だけを使う。
Mac mini はデータを持たないので、本番機への入れ替えは `macmini/setup.sh` の再実行と `worker/.env` のコピーで済む。

```
ファイルサーバー ──SMB読み取り──▶ Mac mini（dwgworker）──HTTP──▶ Ubuntu（Django＋PostgreSQL）◀── ブラウザ
       ▲                                                              │
       └──────────────── SMB読み取り（原本のダウンロード）────────────────┘
```

## Ubuntu のセットアップ

```bash
git clone <このリポジトリ> ~/dwg-find && cd ~/dwg-find
cp .env.example .env && nano .env          # 秘密の値を埋める
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
curl -s -H "Authorization: Bearer $(grep ^WORKER_TOKEN .env | cut -d= -f2)" http://127.0.0.1:8000/api/internal/health
```

- 画面: `http://128.131.250.252:8000/`、管理画面: `/admin/`（辞書の承認、ジョブのやり直し、部品表の修正、監査ログ）
- バックアップ: `ops/backup.sh` を cron で毎日実行

## Mac mini のセットアップ

```bash
git clone <このリポジトリ> ~/dwg-find && cd ~/dwg-find
./macmini/setup.sh                          # 電源・回線・Ollama常駐・接続制限・モデル・Python環境
cp worker/.env.example worker/.env && nano worker/.env
./macmini/setup.sh --worker                 # 解析ワーカーを常駐させる
tail -f ~/Library/Logs/dwgfind-worker.log
```

注意: launchd の常駐設定には `ProcessType=Interactive` が必須（ないと Ollama が 3 tok/s まで落ちる）。

## ワーカーのコマンド

```bash
cd ~/dwg-find/worker
.venv/bin/python -m dwgworker check           # 設定・サーバー・ファイルサーバーへの接続確認
.venv/bin/python -m dwgworker parse 図面.dxf   # 1ファイルだけ解析して結果を表示（サーバー不要）
.venv/bin/python -m dwgworker parse "共有内のパス.dxf" --bom            # 部品表を表の形で表示
.venv/bin/python -m dwgworker bomtest [フォルダ] --kind dxf --limit 30  # 部品表を試しに読んで CSV に保存（DB は変えない）
.venv/bin/python -m dwgworker scan            # 1回巡回
.venv/bin/python -m dwgworker work            # たまったジョブを処理
```

## 開発・テスト

```bash
python3 samples/make_samples.py samples/out   # 架空のサンプル図面を作る
cd worker && python3 -m unittest discover -s tests -t .
cd server && python3 manage.py test drawings  # PostgreSQL（pg_trgm, pgvector）が必要
```

## 進捗

- [x] Phase 0: 機器確認、Mac mini のサーバー化（Ollama 常駐・接続制限・GPU 20 tok/s）
- [x] Phase 1 骨組み: 巡回→ジョブ→解析（表題欄・テキスト・サムネイル）→登録→検索・詳細・ダウンロード・Excel
- [ ] Phase 1 残り: 実際のファイルサーバーへの接続、社内の図番規則と表題欄項目名の反映
- [ ] Phase 2: 部品表の抽出、関連図面、派生系統と部品表差分
- [ ] Phase 3: OCR、Ollama による構造化・辞書育成・検索語展開、意味検索
