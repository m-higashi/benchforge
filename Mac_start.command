#!/bin/bash
# Benchforge 起動(macOS) — ダブルクリックで実行
cd "$(dirname "$0")" || exit 1
export LANG="${LANG:-en_US.UTF-8}"

if [ ! -d ".venv" ]; then
  echo "❌ 仮想環境がありません。先に Mac_setup.command をダブルクリックしてください。"
  read -r -p "Enterキーで終了..."
  exit 1
fi

[ -f config.yaml ] || cp config.example.yaml config.yaml

echo "Benchforge を起動します…"
.venv/bin/python run.py
STATUS=$?
if [ $STATUS -ne 0 ]; then
  echo
  read -r -p "エラーで終了しました。Enterキーで閉じる..."
fi
