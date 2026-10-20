#!/bin/bash
# Benchforge 環境構築(macOS) — ダブルクリックで実行
# Python不要: uv が Python本体と依存パッケージを自動で用意します
cd "$(dirname "$0")" || exit 1
export LANG="${LANG:-en_US.UTF-8}"
echo "============================================"
echo "  Benchforge セットアップ (macOS)"
echo "============================================"
echo

# uv を探す(PATH → 標準インストール先)
UV=""
if command -v uv >/dev/null 2>&1; then
  UV="uv"
elif [ -x "$HOME/.local/bin/uv" ]; then
  UV="$HOME/.local/bin/uv"
fi

# 無ければ uv を自動インストール
if [ -z "$UV" ]; then
  echo "→ uv をインストールしています..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
fi
if [ -z "$UV" ]; then
  echo "❌ uv のインストールに失敗しました。ネットワーク接続を確認して再実行してください。"
  read -r -p "Enterキーで終了..."
  exit 1
fi
echo "✓ $("$UV" --version | head -1) を使用します"

# 仮想環境(Python 3.12 が無ければ uv が自動ダウンロード)
if [ ! -d ".venv" ]; then
  echo "→ Python環境を準備しています(初回はPython本体のダウンロードが入ることがあります)..."
  if ! "$UV" venv .venv --python 3.12; then
    echo "❌ Python環境の作成に失敗しました。ネットワーク接続を確認して再実行してください。"
    read -r -p "Enterキーで終了..."
    exit 1
  fi
fi

echo "→ 依存パッケージ(コア)をインストールしています..."
if ! "$UV" pip install -r requirements.txt --python .venv/bin/python; then
  echo "❌ インストールに失敗しました。ネットワーク接続を確認して再実行してください。"
  read -r -p "Enterキーで終了..."
  exit 1
fi

# config.yaml が無ければ雛形から作成
if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo "✓ config.yaml を作成しました。必要なら results_dir を自分の結果フォルダに書き換えてください。"
fi

echo
echo "============================================"
echo "[完了] セットアップが終わりました。"
echo "       Mac_start.command をダブルクリックで起動してください。"
echo "============================================"
read -r -p "Enterキーで閉じる..."
