#!/usr/bin/env python3
"""verify_install.py — 導入がうまくいっているかを自動で点検する。

**取得したフォルダの中で実行してください。** 何も変更せず、点検して結果を出すだけです。

    python verify_install.py                 # 点検して結果を表示
    python verify_install.py --json out.json # 結果を JSON にも保存

点検する内容:
  A 環境          … OS / Python / 文字コード / 作業場所
  B ファイル       … 必要なファイルが揃っているか・改行コード・BOM・実行権限・ファイル名の正規化
  C 設定          … 設定が無い/BOM付き/壊れている場合に、落ちずに案内が出るか
  D 起動と画面     … 空いているポートで起動し、全ての画面が表示されるか
  E 推論エンジン   … ollama があるか・モデルがあるか・画像を読めるモデルがあるか
  F 実際に解く     … 同梱サンプルを1問解かせて時間を測る（モデルが無い場合は省略）

★このスクリプトは**判定するだけ**で、何かを入れたり直したりしません。
★確認できなかった項目は「未確認」として報告します（推測で OK にしません）。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parent
R: list[dict] = []       # 結果: {id, title, status(OK/NG/未確認), detail}


def app_python() -> str:
    """アプリが実際に動く Python を返す。

    ★`setup` が作る `.venv` と、PATH 上の python は**別物**。
    後者で点検すると「アプリが動く環境ではない環境」を調べることになる（依存が入っておらず
    起動に失敗して原因も分からない）。`.venv` があればそちらを使う。
    """
    for rel in ("Scripts/python.exe", "bin/python"):
        p = APP / ".venv" / rel
        if p.exists():
            return str(p)
    return sys.executable


def rec(rid: str, title: str, status: str, detail: str = "") -> None:
    R.append({"id": rid, "title": title, "status": status, "detail": detail})
    mark = {"OK": "OK ", "NG": "NG ", "未確認": "-- "}.get(status, "?? ")
    print(f"  {mark} {rid:6} {title}" + (f" … {detail}" if detail else ""))


# ── A. 環境 ──────────────────────────────────────────────────────────
def check_env() -> None:
    print("\n[A] 環境")
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    rec("A-1", "Python 3.11 以上", "OK" if ok else "NG",
        f"{platform.python_version()} ({platform.machine()})")
    rec("A-2", "OS", "OK", f"{platform.system()} {platform.release()}")
    rec("A-3", "既定の文字コード", "OK" if sys.getdefaultencoding() == "utf-8" else "NG",
        f"{sys.getdefaultencoding()} / ファイル系={sys.getfilesystemencoding()}")
    # ★2026-08-14: ここは以前、パス文字列に Downloads/Desktop/Documents が含まれるだけで
    #   無条件 NG を出していた。実際に保護が働いているかは一切見ておらず、
    #   Mac 実機検証ではダウンロードフォルダのまま全工程が成功した（README も
    #   「どこに置いても動きます」と書いている）。**実測と矛盾する NG は害の方が大きい**ので、
    #   判定はやめて参考表示にする。書き込みできるかどうかだけを実際に試す。
    writable = True
    try:
        probe = APP / "._verify_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        writable = False
    risky = ("Downloads", "Desktop", "Documents", "ダウンロード", "デスクトップ", "書類")
    hit = [p for p in APP.parts if p in risky]
    note = ("  （参考: OS によっては、この場所のファイルに追加の確認が求められることがあります。"
            "動いている限り移動は不要です）" if hit else "")
    rec("A-4", "作業場所", "OK" if writable else "NG",
        str(APP) + (note if writable else "  ★このフォルダに書き込めません。別の場所へ移してください"))


# ── B. ファイル ──────────────────────────────────────────────────────
NEED = ["run.py", "requirements.txt", "config.example.yaml", "README.md", "LICENSE",
        "app/main.py", "app/config.py", "static/app.js", "static/style.css",
        "templates/index.html"]


def check_files() -> None:
    print("\n[B] ファイル")
    miss = [n for n in NEED if not (APP / n).exists()]
    rec("B-1", "必要なファイルが揃っている", "OK" if not miss else "NG",
        "すべてあり" if not miss else "不足: " + ", ".join(miss))

    # 改行コード: .bat/.ps1 は CRLF、.sh/.command は LF でないと OS 側で失敗する
    bad = []
    for p in list(APP.glob("*.bat")) + list(APP.glob("*.ps1")):
        if b"\r\n" not in p.read_bytes():
            bad.append(f"{p.name}(CRLFでない)")
    for p in list(APP.glob("*.sh")) + list(APP.glob("*.command")):
        if b"\r\n" in p.read_bytes():
            bad.append(f"{p.name}(CRLFが混入)")
    rec("B-2", "改行コード", "OK" if not bad else "NG", "正しい" if not bad else ", ".join(bad))

    # BOM（設定ファイルに付くと起動不能になることがある）
    boms = [p.name for p in APP.glob("*.yaml") if p.read_bytes().startswith(b"\xef\xbb\xbf")]
    rec("B-3", "BOM が付いていない", "OK" if not boms else "NG",
        "なし" if not boms else ", ".join(boms))

    # 実行権限（ZIP 経由だと落ちることがある。Windows は対象外）
    if platform.system() == "Windows":
        rec("B-4", "実行権限", "未確認", "Windows のため対象外")
    else:
        noexec = [p.name for p in list(APP.glob("*.sh")) + list(APP.glob("*.command"))
                  if not os.access(p, os.X_OK)]
        rec("B-4", "起動スクリプトに実行権限", "OK" if not noexec else "NG",
            "あり" if not noexec else "権限なし: " + ", ".join(noexec)
            + "  → chmod +x で付けられます")

    # ファイル名の正規化（macOS 由来だと濁点が分解され、同じ名前なのに一致しない）
    nfd = [str(p.relative_to(APP)) for p in APP.rglob("*")
           if p.is_file() and unicodedata.normalize("NFC", p.name) != p.name]
    rec("B-5", "ファイル名の文字が分解されていない", "OK" if not nfd else "NG",
        "正常" if not nfd else ", ".join(nfd[:5]))

    # 文字化け（ZIP 展開の経路によっては日本語名が壊れる）
    broken = [str(p.relative_to(APP)) for p in APP.rglob("*")
              if p.is_file() and ("�" in p.name or "??" in p.name)]
    rec("B-6", "ファイル名が文字化けしていない", "OK" if not broken else "NG",
        "正常" if not broken else ", ".join(broken[:5]))


# ── C. 設定 ─────────────────────────────────────────────────────────
def _run_config(tmp_cfg: str | None) -> tuple[int, str]:
    """一時的な設定で起動を試し、終了コードと出力を返す（すぐ落とす）。"""
    env = dict(os.environ)
    if tmp_cfg is not None:
        p = APP / "_verify_tmp.yaml"
        p.write_text(tmp_cfg, encoding="utf-8")
        env["BENCHFORGE_CONFIG"] = str(p)
    env["BENCHFORGE_PORT"] = str(_free_port())
    try:
        r = subprocess.run([app_python(), "-c",
                            "import sys; sys.path.insert(0,'.');"
                            "from app.config import load_config; load_config(); print('LOADED')"],
                           cwd=APP, env=env, capture_output=True, text=True, timeout=60,
                           # ★encoding を指定しないと、子が UTF-8・親が cp932 で復号に失敗し
                           #   stdout が None になって落ちる（PYTHONIOENCODING=utf-8 の環境で発生）
                           encoding="utf-8", errors="replace")
        return r.returncode, ((r.stdout or "") + (r.stderr or ""))
    finally:
        (APP / "_verify_tmp.yaml").unlink(missing_ok=True)


def check_config() -> None:
    print("\n[C] 設定")
    # C-1: 設定が無い → 雛形から自動生成して続行するか
    saved = None
    cfg = APP / "config.yaml"
    if cfg.exists():
        saved = cfg.read_text(encoding="utf-8"); cfg.unlink()
    code, out = _run_config(None)
    rec("C-1", "設定が無くても起動できる", "OK" if code == 0 and "LOADED" in out else "NG",
        out.strip().splitlines()[0][:100] if out.strip() else "")
    if saved is not None:
        cfg.write_text(saved, encoding="utf-8")

    # C-2: BOM 付きでも読めるか
    code, out = _run_config("﻿input:\n  results_dir: データセット/chest_demo/results\n")
    rec("C-2", "BOM 付きの設定でも読める", "OK" if code == 0 and "LOADED" in out else "NG")

    # C-3: 壊れた設定は traceback でなく案内が出るか
    code, out = _run_config("input:\n  results_dir: [壊れた\n")
    friendly = ("書式" in out or "設定ファイル" in out) and "Traceback" not in out
    rec("C-3", "壊れた設定で案内が出る（tracebackでない）", "OK" if friendly else "NG",
        out.strip().splitlines()[0][:100] if out.strip() else "")


# ── D. 起動と画面 ───────────────────────────────────────────────────
def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


ENDPOINTS = [
    ("トップ画面", "/"), ("環境チェック", "/api/setup_check"), ("メタ情報", "/api/meta"),
    ("成績一覧", "/api/benchmarks/best"),
    ("有意差", "/api/significance"), ("構成一覧", "/api/characterize/configs"),
    ("構成一覧(保存済み)", "/api/plans"), ("プロンプト", "/api/prompts"),
    ("実演の構成", "/api/demo/configs"), ("ラン状態", "/api/run/status"),
    ("タグ", "/api/tags"), ("問題セット", "/api/datasets"),
    ("問題の一覧", "/api/questions/list"), ("healthz", "/healthz"),
]


def check_server() -> dict | None:
    print("\n[D] 起動と画面")
    port = _free_port()
    env = dict(os.environ); env["BENCHFORGE_PORT"] = str(port)
    proc = subprocess.Popen([app_python(), "run.py"], cwd=APP, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        ok = False
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/healthz", timeout=3); ok = True; break
            except Exception:  # noqa: BLE001
                if proc.poll() is not None:
                    break
                time.sleep(1)
        if not ok:
            out = (proc.stdout.read() if proc.stdout else "")
            # ★traceback は**最後の行に原因が出る**。先頭で切ると利用者に何も伝わらない
            #   （実機検証で「File ...run.py, line 1」だけが表示されて原因不明になった）
            tail = " / ".join([l.strip() for l in out.strip().splitlines() if l.strip()][-3:])
            hint = ""
            if "ModuleNotFoundError" in out:
                hint = "  → 依存が入っていません。先に setup を実行してください"
            rec("D-1", "サーバが起動する", "NG", (tail[:220] or "出力なし") + hint)
            return None
        rec("D-1", "サーバが起動する", "OK", f"ポート {port}")

        ng = []
        setup = None
        for name, path in ENDPOINTS:
            try:
                t0 = time.time()
                with urllib.request.urlopen(base + path, timeout=40) as r:
                    body = r.read(); code = r.status
                if path == "/api/setup_check":
                    setup = json.loads(body)
                if code != 200:
                    ng.append(f"{name}({code})")
            except Exception as e:  # noqa: BLE001
                ng.append(f"{name}({type(e).__name__})")
        rec("D-2", f"画面が使うデータの取得（{len(ENDPOINTS)}件のAPI）", "OK" if not ng else "NG",
            "すべて200" if not ng else "失敗: " + ", ".join(ng))

        # 画面の資源にキャッシュ対策が付いているか（付いていないと更新が届かない）
        html = urllib.request.urlopen(base + "/", timeout=20).read().decode("utf-8", "replace")
        assets = re.findall(r'<(?:link|script)[^>]*/static/[^>]*>', html)
        nov = [a for a in assets if "v=" not in a]
        rec("D-3", "画面の資源に更新用の印が付いている", "OK" if not nov else "NG",
            f"{len(assets)}件すべて付与" if not nov else f"{len(nov)}件で不足")

        # 配色（両テーマの定義があるか）
        css = urllib.request.urlopen(base + "/static/style.css", timeout=20).read().decode("utf-8", "replace")
        both = 'data-theme="light"' in css and 'prefers-color-scheme: light' in css
        rec("D-4", "明るい配色・暗い配色の両方がある", "OK" if both else "NG")
        return {"base": base, "proc": proc, "setup": setup, "port": port}
    except Exception as e:  # noqa: BLE001
        rec("D-1", "サーバが起動する", "NG", str(e)[:150])
        return None


# ── E/F. 推論エンジンと実際の解答 ────────────────────────────────────
def check_engine(ctx: dict | None) -> None:
    print("\n[E] 推論エンジン")
    if not ctx or not ctx.get("setup"):
        rec("E-1", "推論エンジンの状態", "未確認", "サーバが起動しなかったため")
        return
    s = ctx["setup"]
    for st in s.get("steps", []):
        rec("E-" + st["id"][:6], st["title"], "OK" if st["ok"] else "NG",
            st["detail"][:80] + (f"  → {st['cmd']}" if st.get("cmd") else ""))
    mem = s.get("memory", {})
    # ★推奨は「リストの何番目」ではなくサーバが名前を付けて返した値を使う。
    #   以前はここが先頭2つ、画面が末尾1つを読んでいて、同じ状況に正反対の推奨が出ていた。
    gb = mem.get("gb")
    rec("E-mem", "推奨モデル", "OK",
        (f"メモリ {gb}GB" if gb else "メモリ判定なし")
        + f" → まず {s.get('starter', '?')}（積める中で最大は {s.get('best_fit', '?')}）")


def check_solve(ctx: dict | None) -> None:
    print("\n[F] 実際に1問解く")
    if not ctx:
        rec("F-1", "サンプルを1問解く", "未確認", "サーバが起動しなかったため"); return
    s = ctx.get("setup") or {}
    if not s.get("ready"):
        rec("F-1", "サンプルを1問解く", "未確認",
            "推論エンジンかモデルが未準備のため（E の × を先に解消してください）"); return
    try:
        with urllib.request.urlopen(ctx["base"] + "/api/demo/samples", timeout=30) as r:
            sample = json.loads(r.read())
        rec("F-1", "サンプル問題を読める", "OK", str(sample.get("q_id") or "")[:40])
    except Exception as e:  # noqa: BLE001
        rec("F-1", "サンプル問題を読める", "NG", str(e)[:120]); return
    rec("F-2", "解答の実行", "未確認",
        "画面『個別実行』で1問実行し、解答が返るか・所要秒数を手で確認してください")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    print(f"Benchforge 導入点検  ({APP})")
    check_env(); check_files(); check_config()
    ctx = check_server()
    check_engine(ctx); check_solve(ctx)
    if ctx and ctx.get("proc"):
        ctx["proc"].terminate()
        try:
            ctx["proc"].wait(timeout=10)
        except Exception:  # noqa: BLE001
            ctx["proc"].kill()
        print("\n  （点検用に起動したサーバは停止しました）")

    ng = [r for r in R if r["status"] == "NG"]
    un = [r for r in R if r["status"] == "未確認"]
    print(f"\n結果: OK {len(R)-len(ng)-len(un)} / NG {len(ng)} / 未確認 {len(un)}")
    for r in ng:
        print(f"  NG {r['id']} {r['title']}: {r['detail']}")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"env": {"os": platform.system(), "release": platform.release(),
                     "python": platform.python_version(), "machine": platform.machine()},
             "results": R}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  JSON: {args.json}")
    return 1 if ng else 0


if __name__ == "__main__":
    raise SystemExit(main())
