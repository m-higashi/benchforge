"""識別子（プラン名・データセット名など）の共通ルール。★ここが唯一の定義。

Benchforge には「利用者が付けた名前」が2種類ある。混同すると事故になるので分けて扱う:

  A. **ファイル名・URL になる名前** … プラン名 / データセット名 / config_id / prompt_id
     → 半角英数字・`_`・`-` のみ。日本語や記号は使えない。
  B. **中身のラベルにすぎない名前** … タグ名 / split 名 / 説明文
     → JSON の値として保存されるだけなのでどう書いてもよい（日本語推奨）。

A を安全側に倒す関数がここ。⚠**サニタイズは安全機構**（パストラバーサル対策）なので緩めない。
ただし「黙って別名に変える」のは事故のもと（別々の名前が同じファイルに衝突して上書きされる）。
そのため保存経路では `sanitize` ではなく **`validate` で弾く**（呼び出し側の責務）。
"""
from __future__ import annotations

import re

# 使ってよい文字。ファイル名・URL クエリの双方で安全な最小集合。
_UNSAFE = re.compile(r"[^0-9A-Za-z_\-]+")
MAX_LEN = 80

RULE_JA = "半角英数字・ハイフン(-)・アンダースコア(_) のみ"


def sanitize(name: str, fallback: str = "item") -> str:
    """危険な文字を `_` に潰す。**読み取り経路・外部由来の値の防御**に使う。
    ⚠ 別々の入力が同じ結果になりうる（例: 「検証1」も「実験1」も `_1`）ので、
    保存する名前を決めるのに使ってはいけない。保存は validate で弾くこと。"""
    return (_UNSAFE.sub("_", (name or "").strip()) or fallback)[:MAX_LEN]


def is_safe(name: str) -> bool:
    n = (name or "").strip()
    return bool(n) and len(n) <= MAX_LEN and not _UNSAFE.search(n)


def validate(name: str, what: str = "名前") -> str:
    """保存経路用。安全でなければ ValueError（呼び出し側が 400 にする）。"""
    n = (name or "").strip()
    if not n:
        raise ValueError(f"{what}を入力してください。")
    if len(n) > MAX_LEN:
        raise ValueError(f"{what}が長すぎます（{MAX_LEN}文字まで）。")
    if _UNSAFE.search(n):
        bad = "".join(sorted(set(_UNSAFE.findall(n))))[:20]
        raise ValueError(
            f"{what}に使えない文字が含まれています（{bad}）。{RULE_JA}で付けてください"
            f"（保存ファイル名になるため）。例: my_plan / ensemble-judge")
    return n
