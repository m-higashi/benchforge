"""CSV（Excel で作った表）から問題ファイル（YAML）を作る。

★なぜ CSV を「入口」だけにするか（2026-08-07 の設計判断）:
  内部形式を2つにすると、どちらが正かで必ず食い違う。そこで
  **CSV は取り込み時に YAML へ変換して捨てる**。以後は既存の仕組みがそのまま動く。

★実データで測った前提（904問）:
  - 画像は 0〜9枚。**2枚が最多**。→ 列は可変長にし、空欄を許す
  - 画像の 81% に説明文が付く。→ 画像と説明を**対で**受ける
  - **本文は 100% が複数行**。→ セル内改行が必ず出る＝CSV の一番壊れやすい所を毎行踏む
  - 91問に <sup>/<sub> タグ。→ そのまま通す（表示側で処理）

★文字コード:
  日本語版 Excel は CSV を**既定で Shift-JIS(cp932)** に保存する。UTF-8 と決め打つと化ける。
  ここでは**バイト列で受け取り**、UTF-8(BOM有無) → cp932 の順に試す。
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import yaml

from . import naming


class _BlockDumper(yaml.SafeDumper):
    """複数行の文字列を `|-` 形式で書く。
    ★既定のままだと本文が引用符付きの1行風になり、**あとで手で直せない見た目**になる。
      同梱サンプル（手書き）と同じ形にそろえて、CSV 経由でも YAML 直書きでも同じ姿にする。"""


def _str_block(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockDumper.add_representer(str, _str_block)

# 受け付ける列名（表記ゆれを吸収する。小文字化・空白除去して突き合わせ）
ALIASES = {
    "q_id": {"q_id", "qid", "問題id", "問題番号", "id"},
    "question": {"question", "問題文", "設問", "本文"},
    "answer": {"answer", "正解", "解答", "gold"},
}
IMG_RE = re.compile(r"^(?:画像|image|img)\s*(\d+)$", re.IGNORECASE)
# ★列名は「説明」でも「キャプション」でも受ける（2026-08-25）。
#   画面では『その画像のキャプション』と説明しているので、そう書かれても通るようにする。
CAP_RE = re.compile(r"^(?:説明|キャプション|caption|cap)\s*(\d+)$", re.IGNORECASE)

TEMPLATE = (
    "q_id,question,answer,画像1,説明1,画像2,説明2\n"
    "s101,\"次のうち正しいのはどれか。1つ選べ。\n"
    "\n"
    "a 選択肢A\n"
    "b 選択肢B\n"
    "c 選択肢C\",a,s101_img01.jpg,正面,,\n"
    "s102,\"画像を2枚使う問題の例。\n"
    "\n"
    "a 選択肢A\n"
    "b 選択肢B\",b,s102_img01.jpg,正面,s102_img02.jpg,側面\n"
)


def decode(raw: bytes) -> tuple[str, str]:
    """バイト列を文字列に。使った文字コード名も返す（画面に出して安心させるため）。"""
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    # どれでも読めない場合は化けを許容して読む（止めるより中身を見せる方が直しやすい）
    return raw.decode("utf-8", errors="replace"), "不明"


def _norm(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("　", "")


def parse(text: str) -> tuple[list[dict], list[str]]:
    """CSV → 問題の一覧。返り値 (問題リスト, 警告リスト)。"""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return [], ["CSV が空です。"]
    head = [_norm(h) for h in rows[0]]
    col: dict[str, int] = {}
    imgs: list[tuple[int, int]] = []   # (画像番号, 列index)
    caps: dict[int, int] = {}          # 画像番号 -> 列index
    for i, h in enumerate(head):
        for key, names in ALIASES.items():
            if h in names:
                col[key] = i
        m = IMG_RE.match(h)
        if m:
            imgs.append((int(m.group(1)), i))
        m = CAP_RE.match(h)
        if m:
            caps[int(m.group(1))] = i

    warn: list[str] = []
    if "q_id" not in col:
        return [], ["1行目に『q_id』の列がありません。"
                    "先頭行は見出し（q_id, question, answer, 画像1, 説明1 …）にしてください。"]
    if "question" not in col:
        warn.append("『question』列がありません。問題文なしで取り込みます。")

    out: list[dict] = []
    seen: set[str] = set()
    for n, r in enumerate(rows[1:], start=2):
        def cell(i: int | None) -> str:
            return (r[i].strip() if i is not None and i < len(r) else "")

        qid = cell(col.get("q_id"))
        if not qid:
            continue                      # 空行は黙って飛ばす（末尾の空行はよくある）
        try:
            naming.validate(qid, "問題ID")
        except ValueError as e:
            warn.append(f"{n}行目: {e} この行は取り込みません。")
            continue
        if qid in seen:
            warn.append(f"{n}行目: 問題ID『{qid}』が重複しています。この行は取り込みません。")
            continue
        seen.add(qid)
        images = []
        for num, i in sorted(imgs):
            f = cell(i)
            if not f:
                continue
            images.append({"file": f if f.startswith("images/") else f"images/{f}",
                           "caption": cell(caps.get(num))})
        out.append({"q_id": qid, "question": cell(col.get("question")),
                    "answer": cell(col.get("answer")), "images": images})
    if not out:
        warn.append("取り込める行がありませんでした。")
    return out, warn


def write(ds_dir: Path, items: list[dict], overwrite: bool = False) -> dict:
    """問題ファイル（YAML）と正解一覧（answers.csv）を書く。
    ★既存ファイルは既定で**上書きしない**（黙って前の内容を消さないため）。"""
    qdir = ds_dir / "inbox" / "questions"
    qdir.mkdir(parents=True, exist_ok=True)
    created, skipped, updated = [], [], []
    for it in items:
        fp = qdir / f"{it['q_id']}.yaml"
        if fp.exists() and not overwrite:
            skipped.append(it["q_id"])
            continue
        doc: dict = {"q_id": it["q_id"], "question": it["question"]}
        if it["images"]:
            doc["images"] = [{"file": im["file"], "caption": im["caption"]} for im in it["images"]]
        fp.write_text(
            yaml.dump(doc, Dumper=_BlockDumper, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, width=10 ** 6),
            encoding="utf-8")
        (updated if fp.exists() and overwrite else created).append(it["q_id"])

    # 正解一覧は既存を保ちつつ、CSV にあるものだけ差し替える
    ans_written = 0
    have = {it["q_id"]: it["answer"] for it in items if it["answer"]}
    if have:
        ap = ds_dir / "inbox" / "answers.csv"
        cur: dict[str, str] = {}
        if ap.is_file():
            for row in csv.reader(io.StringIO(ap.read_text(encoding="utf-8"))):
                if len(row) >= 2 and _norm(row[0]) not in ALIASES["q_id"]:
                    cur[row[0].strip()] = row[1].strip()
        cur.update(have)
        ans_written = len(have)
        with open(ap, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["q_id", "answer"])
            for k in sorted(cur):
                w.writerow([k, cur[k]])
    return {"created": created, "updated": updated, "skipped": skipped, "answers": ans_written}
