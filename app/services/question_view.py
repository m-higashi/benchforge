"""問題ビューア: 問題文・選択肢・画像を読み取り専用で取得する。

- 本体データ（radbench 相当）: config の questions_dir/<year>/<no:03d>.yaml と同ディレクトリの画像。
- 自前データ（standalone）: データセット/<dataset>/inbox/questions/<qid>.yaml と images/。

画像は data-URI 化して返す（別途の画像配信口を作らず、1レスポンスで完結）。
"""
from __future__ import annotations

import base64
import re
import unicodedata
from pathlib import Path

import yaml

from app.services import standalone as SA

_CHOICE = re.compile(r"^\s*([a-eａ-ｅ])[\s.．:：)）、]+(.+?)\s*$")


def _parse_choices(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        m = _CHOICE.match(line)
        if not m:
            continue
        k = unicodedata.normalize("NFKC", m.group(1)).lower()
        if k in "abcde":
            out.append({"key": k, "text": m.group(2).strip()})
    return out


def _stem_only(text: str) -> str:
    """選択肢行を除いた設問本文。"""
    lines = [l for l in (text or "").splitlines() if not _CHOICE.match(l)]
    return "\n".join(lines).strip()


def _img_data_uri(p: Path) -> str | None:
    if not p.is_file():
        return None
    ext = p.suffix.lstrip(".").lower() or "png"
    if ext == "jpg":
        ext = "jpeg"
    return f"data:image/{ext};base64," + base64.b64encode(p.read_bytes()).decode("utf-8")


class QuestionFileError(ValueError):
    """問題ファイルが読めないときに投げる（利用者に見せる文面をそのまま持つ）。"""


def _load_yaml(ypath: Path) -> dict:
    """問題ファイルを読む。書式が壊れていたら**どこが悪いか**を添えて投げ直す。
    ★手で書き換える前提のファイルなので、素の ScannerError を外に出さない（2026-08-07）。
      実測で多かった壊し方: タブでの字下げ／全角スペースでの字下げ／本文中の「:」。
      いずれも読み込みが例外で落ち、画面には「Internal Server Error」しか出なかった。"""
    try:
        return yaml.safe_load(ypath.read_text(encoding="utf-8")) or {}
    except UnicodeDecodeError:
        raise QuestionFileError(
            f"問題ファイル『{ypath.name}』の文字コードが UTF-8 ではありません。"
            "テキストエディタで「UTF-8」を選んで保存し直してください。") from None
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"{mark.line + 1}行目{mark.column + 1}文字目" if mark else "場所不明"
        raw = ypath.read_text(encoding="utf-8", errors="replace").splitlines()
        line = raw[mark.line].rstrip() if mark and mark.line < len(raw) else ""
        # ★エラー位置が本文の外を指すことがある（全角スペース字下げで実際に起きた）。
        #   その場合はファイル全体を見て、よくある壊し方を探す。
        bad_indent = next((ln for ln in raw if ln[:1] in ("\t", "　")), "")
        if not line:
            line = bad_indent.rstrip()
        hint = ""
        if "\t" in line:
            hint = "字下げに Tab が使われています。**半角スペース**に置き換えてください。"
        elif "　" in line:
            hint = "字下げに全角スペースが使われています。**半角スペース**に置き換えてください。"
        elif ":" in line and not line.lstrip().startswith("-"):
            hint = ("本文の中に「:」があります。"
                    "本文は `question: |-` と書いて、次の行から半角スペース2つ下げて書いてください。")
        raise QuestionFileError(
            f"問題ファイル『{ypath.name}』の書式が正しくありません（{where}）。"
            + (f"\n  該当行: {line}" if line else "")
            + (f"\n  {hint}" if hint else "")
            + "\n  同梱の chest_demo/inbox/questions/s001.yaml が書き方の見本です。") from None


def _from_yaml(ypath: Path, img_dir: Path, img_files: list[str] | None) -> dict:
    doc = _load_yaml(ypath)
    qtext = str(doc.get("question", ""))
    images, caps = [], []
    files = img_files if img_files is not None else [im.get("file", "") for im in (doc.get("images") or [])]
    # ★問題ファイルに images: が無ければ、命名規則 <問題ID>_img<NN> で置かれた画像を拾う（2026-08-07）。
    #   統計側(standalone.image_counts)は元々ファイル名から枚数を数えており、
    #   書き忘れると**「画像2枚の問題」と集計されているのにモデルには0枚しか渡らない**という
    #   気づけない食い違いが起きていた（実測で確認）。両者の根拠を揃える。
    if not files:
        qid = str(doc.get("q_id") or ypath.stem)
        cand = sorted((img_dir / "images").glob(f"{qid}_img*")) if (img_dir / "images").is_dir() else []
        files = [str(c.relative_to(img_dir)) for c in cand
                 if c.suffix.lower() in (".jpg", ".jpeg", ".png")]
    for f in files:
        if not f:
            continue
        ip = (img_dir / f) if not Path(f).is_absolute() else Path(f)
        uri = _img_data_uri(ip)
        if uri:
            images.append(uri)
    for im in (doc.get("images") or []):
        caps.append(im.get("caption", ""))
    return {"stem": _stem_only(qtext), "choices": _parse_choices(qtext),
            "images": images, "captions": caps, "raw": qtext}


def get_question(qid: str, dataset: str, questions_dir: Path | None) -> dict | None:
    """dataset="" or "radbench" は questions_dir から、それ以外は データセット/<dataset> から読む。"""
    if dataset and dataset != "radbench":
        ds = SA.dataset_dir(dataset)
        yp = ds / "inbox" / "questions" / f"{qid}.yaml"
        if not yp.is_file():
            return None
        return {"q_id": qid, **_from_yaml(yp, yp.parent, None)}
    # 外部データ: qid="<年>-<番号>" → questions_dir/<年>/<番号3桁>.yaml、画像は同ディレクトリ。
    if not questions_dir:
        return None
    m = re.match(r"^(\d{4})[-_](\d+)$", qid)
    if not m:
        return None
    year, no = m.group(1), int(m.group(2))
    ydir = questions_dir / year
    yp = None
    for name in (f"{no:03d}.yaml", f"{no}.yaml", f"{no:03d}.yml", f"{no}.yml"):
        if (ydir / name).is_file():
            yp = ydir / name
            break
    if yp is None:
        return None
    # 同ディレクトリの <no:03d>-image*.(jpg|png) をソートして採用
    imgs = sorted(p.name for p in ydir.glob(f"{no:03d}-image*")
                  if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    return {"q_id": qid, **_from_yaml(yp, ydir, imgs)}
