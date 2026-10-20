"""setup_check — 「はじめて動かす人」のための環境診断。

対象は **ローカルLLMを触ったことがない人**。つまずく所は毎回ほぼ同じなので、
「動かない」ではなく **何が足りなくて、次に何を打てばよいか** を返す。

診断すること:
  1. 推論エンジン(ollama)が動いているか        → 入れ方・起動のしかたを案内
  2. モデルが1つでも入っているか                → `ollama pull ...` をそのまま案内
  3. **画像を読めるモデル(vision)があるか**      → ★初心者が最も踏む罠。
     テキスト専用モデルを選ぶと画像が黙って無視され、成績だけが低く出る
  4. 使えるメモリ(VRAM/統合メモリ)の目安        → 積めるモデルの大きさを提示
  5. 解析対象の結果ファイルがあるか              → 無くても「まず解かせる」導線を出す

★ここは**判定を返すだけ**で、勝手に何かを入れたり起動したりしない（利用者が選ぶ）。
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

# 画像を読めるモデルの見分け（タグ名の慣習で判定する。確実ではないので「たぶん」と表示する）
VISION_HINTS = ("vl", "vision", "llava", "gemma3", "gemma4", "qwen2.5vl", "qwen3-vl",
                "minicpm-v", "moondream", "pixtral", "internvl")

# メモリ別の推奨。**個人で買える範囲**を上から順に並べる。
#   必要メモリの目安 = パラメータ数(B) × 0.6GB（4bit量子化のざっくり値）＋ 画像処理の余白
RECOMMEND = [
    {"vram": 6,  "tag": "qwen2.5vl:3b",  "size_gb": 3.2,
     "note": "最小構成。まず動かして雰囲気をつかむ用"},
    {"vram": 8,  "tag": "qwen2.5vl:7b",  "size_gb": 6.0,
     "note": "8GBのGPU（RTX 4060 等）で動く実用の下限"},
    {"vram": 12, "tag": "gemma4:12b",    "size_gb": 7.4,
     "note": "12GB（RTX 4070 等）。画像の読みが安定してくる"},
    {"vram": 24, "tag": "gemma4:31b",    "size_gb": 18.0,
     "note": "24GB（RTX 4090 / 統合メモリ機）。本格的に使うならここから"},
]


def _get(url: str, timeout: float = 4.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _ollama(base_url: str) -> dict:
    """推論エンジンの状態。動いていなければ入れ方を案内する。"""
    base = (base_url or "http://127.0.0.1:11434").rstrip("/")
    try:
        ver = _get(f"{base}/api/version").get("version", "?")
        return {"ok": True, "version": ver, "url": base}
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        installed = shutil.which("ollama") is not None
        return {
            "ok": False, "url": base, "installed": installed,
            "how": ("ollama は入っているようですが、応答がありません。"
                    "ターミナルで `ollama serve` を実行して起動したままにしてください。")
            if installed else
            ("推論エンジン ollama が見つかりません。https://ollama.com からインストーラを入れて "
             "実行してください（Windows / macOS / Linux）。入れると自動で起動します。"),
        }


def _models(base_url: str) -> list[dict]:
    base = (base_url or "http://127.0.0.1:11434").rstrip("/")
    try:
        ms = _get(f"{base}/api/tags", timeout=8).get("models", [])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for m in ms:
        name = m.get("model") or m.get("name") or ""
        low = name.lower()
        out.append({
            "tag": name,
            "size_gb": round((m.get("size") or 0) / 1e9, 1),
            "vision": any(h in low for h in VISION_HINTS),
        })
    return sorted(out, key=lambda x: -x["size_gb"])


_UNIFIED = [""]   # 統合メモリ機なら GPU 名が入る（nvidia-smi が容量を返さない機種）


def _kind_label() -> str:
    """本体メモリを返すときの説明文。統合メモリ機かどうかで言い方を変える。"""
    if _UNIFIED[0]:
        return f"本体メモリ（{_UNIFIED[0]} と共有する統合メモリ・全量をモデルには使えません）"
    return "本体メモリ（GPUを検出できず）"


def _memory_gb() -> dict:
    """使えるメモリの目安。GPUが見えなければ本体メモリで代替する（統合メモリ機も拾える）。

    ★2026-08-14 修正: 以前は NVIDIA GPU と `/proc/meminfo`（Linux 専用）しか見ておらず、
      **macOS と Windows では必ず None** になっていた。その結果 `_fit` の「メモリ不明」経路に落ち、
      16GB の Mac に 18GB のモデルを勧めるところまで繋がっていた（Mac 実機検証 2026-08-14）。
      docstring は「統合メモリ機も拾える」と謳っていたが、統合メモリ機＝Apple Silicon こそ
      /proc を持たないため絶対に拾えなかった。OS ごとの経路を明示的に持たせる。
    """
    # 1) NVIDIA GPU（載っていれば、それが実際に使える量）
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.total",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        v = r.stdout.strip().splitlines()[0].strip()
        if v and v.upper() not in ("N/A", "[N/A]"):
            return {"gb": round(float(v) / 1024, 1), "kind": "GPU (NVIDIA)"}
        # ★統合メモリ機（NVIDIA GB10 等）は memory.total が [N/A] を返す（2026-08-25）。
        #   GPU は載っているのに「検出できず」と出ると故障に見えるため、名前を引いて区別する。
        #   この種の機械は本体メモリを GPU と共有しているので、後段の本体メモリ値が実質正しい。
        rn = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                            capture_output=True, text=True, timeout=5)
        if rn.stdout.strip():
            _UNIFIED[0] = rn.stdout.strip().splitlines()[0].strip()
    except Exception:  # noqa: BLE001
        pass
    system = platform.system()
    # 2) macOS（Apple Silicon の統合メモリを含む）
    if system == "Darwin":
        try:
            r = subprocess.run(["sysctl", "-n", "hw.memsize"],
                               capture_output=True, text=True, timeout=5)
            b = int(r.stdout.strip())
            if b > 0:
                # ★統合メモリは全部をモデルに使えるわけではない（既定でおおよそ 2/3 程度）。
                #   総量をそのまま返すと過大な推奨になるため、kind に但し書きを添える。
                return {"gb": round(b / 1024 ** 3, 1),
                        "kind": "本体メモリ（統合メモリ・全量をモデルには使えません）"}
        except Exception:  # noqa: BLE001
            pass
    # 3) Windows
    if system == "Windows":
        try:
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            ms = _MS(); ms.dwLength = ctypes.sizeof(_MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return {"gb": round(ms.ullTotalPhys / 1024 ** 3, 1),
                        "kind": _kind_label()}
        except Exception:  # noqa: BLE001
            pass
    # 4) Linux ほか
    try:
        mem = Path("/proc/meminfo").read_text()
        for line in mem.splitlines():
            if line.startswith("MemTotal:"):
                return {"gb": round(float(line.split()[1]) / 1024 / 1024, 1),
                        "kind": _kind_label()}
    except Exception:  # noqa: BLE001
        pass
    # 5) 最後の砦（POSIX 共通。上のどれかで拾えるはずだが、取りこぼしを潰す）
    try:
        import os
        b = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        if b > 0:
            return {"gb": round(b / 1024 ** 3, 1), "kind": _kind_label()}
    except Exception:  # noqa: BLE001
        pass
    return {"gb": None, "kind": "不明"}


def _fit(mem_gb: float | None) -> list[dict]:
    """積めそうなモデルを**必ず大きい順**に返す。

    ★2026-08-14 修正: メモリ不明のときだけ `return RECOMMEND`（＝昇順のまま）にしていたため、
      「末尾＝最小」を前提にした呼び出し側が **最大のモデルを勧めていた**。
      向きの取り決めを1箇所（この関数）に閉じ込め、呼び出し側は starter/best_fit を使う。
    """
    if not mem_gb:
        return list(reversed(RECOMMEND))
    ok = [r for r in RECOMMEND if r["vram"] <= mem_gb + 0.5]
    return list(reversed(ok)) if ok else [RECOMMEND[0]]


def pick_default_model(base_url: str) -> str:
    """実際に入っているモデルから「画像を読める1つ」を選ぶ。無ければ空文字。

    ★構成にモデルが書かれていないときの穴埋めに使う。実在するタグを構成ファイルに
      直書きすると、そのモデルを持っていない人が別の失敗を踏むため、**出荷時は空のまま**にし
      実行時にこの関数で埋める（Mac 実機検証 2026-08-14 で 11分の空転を生んだ箇所）。

    ★2026-08-14 改訂: 以前は**一覧で最小**のモデルを選んでいた（初回が速く終わる方がよいと考えた）。
      しかし Windows 実機で 3B が選ばれ、「1つ選べ」の問題に `a, b, c, e` と返して
      **解答契約に従えず棄権**になった。小さすぎるモデルは初回体験をむしろ壊す。
      ⇒ **積める範囲で最大**を選ぶ。メモリが判定できないときだけ、安全側に最小へ倒す。
    """
    try:
        models = _models(base_url)
    except Exception:  # noqa: BLE001
        return ""
    pool = [m for m in models if m["vision"]] or models
    if not pool:
        return ""
    gb = _memory_gb().get("gb")
    if gb:
        # 実測サイズで判定する（推奨表の目安ではなく、実際に入っている物の大きさ）。
        # 係数 0.7 = 統合メモリ機で全量をモデルに使えないこと・作業領域ぶんの余裕。
        fits = [m for m in pool if m["size_gb"] and m["size_gb"] <= gb * 0.7]
        if fits:
            # ★上限を設ける。「積める範囲で最大」だけだと、大きなモデルを多数持つ機体で
            #   62GB の重量級が自動採用され、初回が終わらないほど遅くなる（実測で発生）。
            #   自分たちが推奨表に載せている上限（現 18GB）を超えるものは自動では選ばない
            #   ＝それより大きい物を使いたい人は構成で明示的に指定する、という切り分け。
            cap = max(r["size_gb"] for r in RECOMMEND)
            sane = [m for m in fits if m["size_gb"] <= cap]
            return max(sane or fits, key=lambda m: m["size_gb"])["tag"] if sane \
                else min(fits, key=lambda m: m["size_gb"])["tag"]
    return min(pool, key=lambda m: m["size_gb"])["tag"]


def diagnose(base_url: str, results_dir: Path) -> dict:
    """全体診断。UI はこの戻り値をそのまま並べれば案内になる。"""
    eng = _ollama(base_url)
    models = _models(base_url) if eng["ok"] else []
    vision = [m for m in models if m["vision"]]
    mem = _memory_gb()
    fit = _fit(mem["gb"])           # 必ず大きい順
    # ★「リストのどちら端を読むか」を各所で判断させない。ここで名前を付けて配る。
    #   （画面は末尾＝最小、点検スクリプトは先頭2つ、と読み方が食い違って
    #     同じ状況に正反対の推奨が出ていた・Mac 実機検証 2026-08-14）
    starter = fit[-1]["tag"] if fit else RECOMMEND[0]["tag"]   # まず入れる1つ（最小）
    best_fit = fit[0]["tag"] if fit else RECOMMEND[0]["tag"]   # 積める中で最大
    trials = Path(results_dir) / "trials.jsonl"

    steps = []
    steps.append({
        "id": "engine", "title": "推論エンジン（ollama）",
        "ok": eng["ok"],
        "detail": f"動作中（{eng.get('version')}）" if eng["ok"] else eng.get("how", ""),
        # ★入っていない人に `ollama serve` を渡さない（実行できないコマンドを配ると詰まる）。
        #   入っているのに応答が無いときだけ起動コマンドを出す。
        "cmd": "" if eng["ok"] else ("ollama serve" if eng.get("installed") else ""),
    })
    steps.append({
        "id": "model", "title": "モデルが入っているか",
        "ok": bool(models),
        "detail": (f"{len(models)} 個入っています" if models
                   else "1つも入っていません。まず小さいものを1つ入れてください"),
        "cmd": "" if models else f"ollama pull {starter}",
    })
    steps.append({
        "id": "vision", "title": "画像を読めるモデルがあるか",
        "ok": bool(vision),
        # ★ここが初心者の最大の罠。テキスト専用モデルだと画像が黙って無視される
        "detail": (f"あります: {', '.join(m['tag'] for m in vision[:3])}" if vision
                   else "画像を読めるモデルが見当たりません。画像つきの問題を解かせるには必要です"),
        "cmd": "" if vision else f"ollama pull {starter}",
    })
    steps.append({
        "id": "memory", "title": "使えるメモリ",
        "ok": bool(mem["gb"]),
        "detail": (f"{mem['gb']} GB（{mem['kind']}）" if mem["gb"]
                   else "判定できませんでした。小さいモデルから試してください"),
        "cmd": "",
    })
    steps.append({
        "id": "results", "title": "解析できる結果があるか",
        "ok": trials.is_file(),
        "detail": ("あります。『成績一覧』から見られます" if trials.is_file()
                   else "まだありません。『個別実行』で1問解かせるところから始められます"),
        "cmd": "",
    })

    ready = all(s["ok"] for s in steps if s["id"] in ("engine", "model", "vision"))
    return {
        "ready": ready,
        "steps": steps,
        "models": models,
        "memory": mem,
        "recommend": fit,          # 大きい順（表示用）
        "starter": starter,        # まず入れる1つ＝最小
        "best_fit": best_fit,      # 積める中で最大
        "next": ("『個別実行』タブで、同梱のサンプル問題を1問解かせてみてください。"
                 if ready else "上の × が付いた項目を順に片付けてください。"),
    }
