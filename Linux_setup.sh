#!/usr/bin/env bash
# Benchforge 環境構築(Linux) — uv で Python本体＋依存を用意
set -uo pipefail
cd "$(dirname "$0")" || exit 1

echo "=== Benchforge セットアップ (Linux) ==="

UV=""
if command -v uv >/dev/null 2>&1; then UV="uv"
elif [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"; fi
if [ -z "$UV" ]; then
  echo "→ uv をインストールします..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
fi
[ -n "$UV" ] || { echo "❌ uv 導入失敗。ネットワークを確認してください。"; exit 1; }
echo "✓ $("$UV" --version | head -1)"

[ -d .venv ] || "$UV" venv .venv --python 3.12 || { echo "❌ venv作成失敗"; exit 1; }
"$UV" pip install -r requirements.txt --python .venv/bin/python || { echo "❌ 依存導入失敗"; exit 1; }

if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo "✓ config.yaml を作成（results_dir を必要に応じて編集）"
fi
echo "[完了] ./start.sh で起動できます。"
