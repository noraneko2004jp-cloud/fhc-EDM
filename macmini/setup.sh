#!/usr/bin/env bash
# Mac mini を DWG-FIND の解析ノードにする（仮機・本番機共通、何度実行してもよい）
#   ./macmini/setup.sh            OS設定・Ollama・接続制限・モデル・ワーカーの準備
#   ./macmini/setup.sh --worker   上に加えて解析ワーカーを常駐させる（worker/.env を用意してから）
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"
UBUNTU_IP="${UBUNTU_IP:-128.131.250.252}"
SELF_IP="$(ipconfig getifaddr en0 || true)"
LD=/Library/LaunchDaemons
say(){ printf '\n== %s ==\n' "$*"; }

[ -n "$SELF_IP" ] || { echo "有線(en0)のIPが取れません。有線LANを確認してください"; exit 1; }
echo "リポジトリ: $REPO / ユーザー: $USER_NAME / このMac: $SELF_IP / Ubuntu: $UBUNTU_IP"

say "電源と回線"
sudo pmset -a autorestart 1 sleep 0 disksleep 0
sudo networksetup -setairportpower en1 off 2>/dev/null || true
sudo networksetup -setnetworkserviceenabled Wi-Fi off 2>/dev/null || true

install_plist(){ # $1=テンプレート $2=ラベル
  sed -e "s|__USER__|$USER_NAME|g" -e "s|__REPO__|$REPO|g" "$1" | sudo tee "$LD/$2.plist" >/dev/null
  sudo chown root:wheel "$LD/$2.plist"; sudo chmod 644 "$LD/$2.plist"
  plutil -lint "$LD/$2.plist" >/dev/null || { echo "$2.plist の書式が不正です"; exit 1; }
  sudo launchctl bootout "system/$2" 2>/dev/null || true
  for i in $(seq 1 10); do sudo launchctl print "system/$2" >/dev/null 2>&1 || break; sleep 1; done  # 停止完了を待つ
  sudo launchctl enable "system/$2"
  for i in 1 2 3; do sudo launchctl bootstrap system "$LD/$2.plist" && return 0; sleep 2; done
  echo "$2 を起動できませんでした。次の出力を確認してください:"; sudo launchctl print "system/$2" 2>&1 | head -20; exit 1
}

say "Ollama（ログインなしで起動・GPU優先）"
[ -x /Applications/Ollama.app/Contents/Resources/ollama ] || { echo "Ollama.app を /Applications に入れてから再実行してください"; exit 1; }
osascript -e 'quit app "Ollama"' 2>/dev/null || true
pkill -f "Ollama.app/Contents/MacOS" 2>/dev/null || true
install_plist "$REPO/macmini/launchd/com.dwgfind.ollama.plist" com.dwgfind.ollama

say "接続制限（Ollama へは このMac と Ubuntu だけ）"
sed -e "s|__SELF_IP__|$SELF_IP|g" -e "s|__UBUNTU_IP__|$UBUNTU_IP|g" "$REPO/macmini/pf/pf.dwgfind.conf" | sudo tee /etc/pf.dwgfind.conf >/dev/null
sudo pfctl -nf /etc/pf.dwgfind.conf
install_plist "$REPO/macmini/launchd/com.dwgfind.pf.plist" com.dwgfind.pf

say "モデル"
for i in $(seq 1 20); do curl -sf http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
grep -vE '^\s*(#|$)' "$REPO/macmini/models.txt" | while read -r m; do /Applications/Ollama.app/Contents/Resources/ollama pull "$m"; done

say "解析ワーカーの Python 環境"
command -v brew >/dev/null || { echo "Homebrew が必要です"; exit 1; }
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
PY="$(brew --prefix python@3.12)/bin/python3.12"
[ -d "$REPO/worker/.venv" ] || "$PY" -m venv "$REPO/worker/.venv"
"$REPO/worker/.venv/bin/pip" install -q --upgrade pip
"$REPO/worker/.venv/bin/pip" install -q -r "$REPO/worker/requirements.txt"
if ! (cd "$REPO/worker" && .venv/bin/python -m unittest discover -s tests -t . >/tmp/dwgfind-test.log 2>&1); then
  grep -v "point size" /tmp/dwgfind-test.log | tail -30
  echo "自動テストが失敗しました。上の内容を確認してください（全文: /tmp/dwgfind-test.log）"; exit 1
fi
echo "自動テスト OK"

if [ "${1:-}" = "--worker" ]; then
  say "解析ワーカーを常駐"
  [ -f "$REPO/worker/.env" ] || { echo "worker/.env がありません（worker/.env.example をコピーして編集）"; exit 1; }
  (cd "$REPO/worker" && .venv/bin/python -m dwgworker check)
  install_plist "$REPO/macmini/launchd/com.dwgfind.worker.plist" com.dwgfind.worker
  # 初回起動時は macOS の「ローカルネットワーク」許可が反映される前に通信して失敗することがある。
  # 失敗を見つけたら 1 回だけ再起動する（2026-09-29 仮機で確認）
  LOG="/Users/$USER_NAME/Library/Logs/dwgfind-worker.log"
  sleep 20
  if tail -20 "$LOG" 2>/dev/null | grep -q "No route to host"; then
    echo "Ubuntu に届かなかったため再起動します。直らない場合は システム設定 > プライバシーとセキュリティ > ローカルネットワーク で python3.12 をオンにしてください"
    sudo launchctl kickstart -k system/com.dwgfind.worker
    sleep 20
  fi
  tail -3 "$LOG" 2>/dev/null || true
  echo "ログ: tail -f ~/Library/Logs/dwgfind-worker.log"
fi
say "完了"
