"""Benchforge 設定ローダ。config.yaml を読むだけ（radbench 非依存）。"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

APP_ROOT = Path(__file__).resolve().parent.parent  # Benchforge/


def load_config() -> dict:
    """設定を読む。**設定が無い・壊れていても、traceback で落とさない。**

    ここは「はじめて起動する人が最初に踏む場所」なので、失敗の仕方が体験を決める:
      1. `config.yaml` が無い → `config.example.yaml` から**自動生成して続行**（案内を1行出す）
      2. BOM 付きで保存されている → **黙って取り除く**
         （メモに貼って保存すると付き、パーサが1行目で構文エラーになって起動不能になる）
      3. 書式が壊れている → **traceback ではなく「どこが・どう悪いか」を出して終了**
    """
    cfg_path = Path(os.environ.get("BENCHFORGE_CONFIG", APP_ROOT / "config.yaml"))

    if not cfg_path.is_file():
        example = APP_ROOT / "config.example.yaml"
        if example.is_file():
            cfg_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"設定ファイルが無かったので、雛形から作成しました: {cfg_path}\n"
                  f"  そのまま起動します。入力元を変えたい場合はこのファイルを編集してください。")
        else:
            raise SystemExit(
                f"設定ファイルが見つかりません: {cfg_path}\n"
                f"  雛形 config.example.yaml も見当たりません。配布物が壊れている可能性があります。\n"
                f"  再ダウンロードするか、config.yaml を作成してください。")

    text = cfg_path.read_text(encoding="utf-8-sig")   # utf-8-sig = BOM があれば取り除く
    try:
        cfg = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"{mark.line + 1} 行目 {mark.column + 1} 文字目" if mark else "位置不明"
        raise SystemExit(
            f"設定ファイルの書式が正しくありません: {cfg_path}\n"
            f"  場所: {where}\n"
            f"  内容: {getattr(e, 'problem', e)}\n"
            f"  ヒント: 「キー: 値」の形で書きます（コロンの後に半角スペースが要ります）。\n"
            f"          分からなくなったら config.yaml を削除してください。雛形から作り直されます。")
    if not isinstance(cfg, dict):
        raise SystemExit(f"設定ファイルの中身が正しくありません: {cfg_path}\n"
                         f"  「キー: 値」の並びで書いてください。削除すれば雛形から作り直されます。")
    cfg["_config_file"] = str(cfg_path)

    # 環境変数による上書き（systemd などから起動するとき用）
    srv = cfg.setdefault("server", {})
    if os.environ.get("BENCHFORGE_HOST"):
        srv["host"] = os.environ["BENCHFORGE_HOST"]
    if os.environ.get("BENCHFORGE_PORT"):
        srv["port"] = int(os.environ["BENCHFORGE_PORT"])
    return cfg


def results_dir(cfg: dict) -> Path:
    """入力源(results/)。相対指定は APP_ROOT 起点で解決（パッケージ移植性のため）。"""
    p = Path(cfg.get("input", {}).get("results_dir", ""))
    return p if p.is_absolute() else (APP_ROOT / p)


def plans_dir(cfg: dict) -> Path:
    """構成(プラン)保存先。相対指定は APP_ROOT 起点で解決。"""
    p = Path(cfg.get("data", {}).get("plans_dir", "data/plans"))
    return p if p.is_absolute() else (APP_ROOT / p)


def questions_dir(cfg: dict) -> Path | None:
    """問題ビューア用の問題ディレクトリ（読み取り専用・任意）。未設定なら None。"""
    raw = cfg.get("input", {}).get("questions_dir", "")
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else (APP_ROOT / p)


def ollama_url(cfg: dict) -> str:
    return cfg.get("ollama", {}).get("url", "http://localhost:11434")




def prompts_file(cfg: dict) -> Path:
    """プロンプトライブラリ(JSON)のパス。data/ 直下に置く。"""
    return plans_dir(cfg).parent / "prompts.json"
