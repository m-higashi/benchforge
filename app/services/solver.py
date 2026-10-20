"""非ストリームのソルバ: 構成のステージ列を自前 ollama で順次実行し、最終解答を抽出する。

「個別実行」タブ(/api/demo/run)のストリーム版と同じステージ処理の非ストリーム版。
独立ランナー(StandaloneRunnerBackend._solve)から呼ぶ。GPU(自前 ollama)を使う。
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from app.services import ollama_client as OC

_VISION_STAGES = ("direct_mc", "vision_findings")
_ANSWER_STAGES = ("direct_mc", "reason")


def extract_answer(text: str) -> str:
    """最終行の「解答: X」等から選択肢記号を抽出（a–e・複数可）。

    ★接頭辞を必須にしている（緩めない）＝2026-08-14 の判断。
      「接頭辞が無くても最終行が記号だけなら拾う」案を検討したが**採らなかった**。
      実例: 「1つ選べ」の問題に対しモデルが `a, b, c, e` と4つ返した。これを解答として
      拾うと「4択を選んだ誤答」になり、**モデルが解答契約に従っていない事実が
      棄権率から消える**。棄権の追跡は、思考モードで本文が空になる事故を検出できた
      仕組みそのものなので、見かけの棄権率を下げるために潰さない。
      形式に従わせたい場合はプロンプト側で対処する。
    ⚠ この関数は解答抽出の**唯一の定義**。app/main.py はここを import している
      （以前は同じ正規表現の写しがあり、片方だけ直すと採点が食い違う状態だった）。
    """
    t = unicodedata.normalize("NFKC", text or "")
    m = list(re.finditer(r"(?:最終解答|解答|答え|answer)\s*[:：]\s*([a-e\s,、，/]+)", t, re.IGNORECASE))
    if not m:
        return ""
    return "".join(sorted(set(re.findall(r"[a-e]", m[-1].group(1).lower()))))


class _Default(dict):
    def __missing__(self, k):
        return ""


def _fill(template: str, **kw) -> str:
    return (template or "").format_map(_Default(**kw))


DEFAULT_NUM_PREDICT = 2048   # 出力の上限トークン数。思考モードは 6144 程度が必要
DEFAULT_NUM_CTX = 32768      # 文脈長。画像や長い指示文もここを消費する


def fill_missing_models(base_url: str, stages: list[dict]) -> tuple[list[dict], str]:
    """モデルが空のステージを、実際に入っているモデルで埋める。返り値 (stages, 採用したタグ)。

    ★なぜ実行時に埋めるのか（Mac 実機検証 2026-08-14）:
      同梱の構成は `"model": ""` で出荷している。README の順どおりに進んだ利用者は
      **AI が一度も呼ばれないまま「解答を抽出できませんでした」**と言われ、11分空転した。
      かといって実在タグを直書きすると、そのモデルを持っていない人が別の失敗を踏む。
      ⇒ 出荷時は空のままにし、**実行する時点で入っているモデルから選んで埋める**。
      何を使ったかは必ず画面に出すこと（黙って選ぶと、何で測った結果か分からなくなる）。
    """
    if not any(st.get("type") not in ("vote", "aggregate") and not st.get("model")
               for st in (stages or [])):
        return stages, ""
    from app.services import setup_check as SC
    tag = SC.pick_default_model(base_url)
    if not tag:
        return stages, ""
    out = []
    for st in (stages or []):
        if st.get("type") not in ("vote", "aggregate") and not st.get("model"):
            st = {**st, "model": tag, "_model_auto": True}
        out.append(st)
    return out, tag


def solve_question(base_url: str, prompts: dict, stages: list[dict],
                   question: str, captions: str = "", images_b64: list[str] | None = None,
                   temperature: float = 0.0, timeout: float = 300.0) -> dict:
    """1問を構成のステージ列で解く。返り値: {pred:list[str], final:str, stage_answers:list}。
    images_b64 は data-URI でも生 base64 でも可（自動で prefix を除去）。"""
    imgs = [(i.split(",", 1)[-1] if isinstance(i, str) and i.startswith("data:") else i)
            for i in (images_b64 or []) if i]
    ctx = {"question": question, "captions": captions, "findings": "", "wiki": "", "aux": ""}
    cand: list[str] = []
    final = ""
    stage_answers = []
    for st in stages:
        stype = st.get("type")
        if stype in ("vote", "aggregate"):
            voters = [c for c in cand if c]
            ans = ""
            if voters:
                cnt = Counter(voters); top = max(cnt.values())
                ans = next(v for v in voters if cnt[v] == top)
            final = ans
            stage_answers.append({"type": stype, "answer": ans})
            continue
        model = st.get("model")
        if not model:
            stage_answers.append({"type": stype, "answer": "", "note": "モデル未指定→スキップ"})
            continue
        p = prompts.get(st.get("prompt_id", ""), {})
        system = p.get("system", "")
        user = _fill(p.get("user", "{question}"), **ctx)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if stype in _VISION_STAGES and imgs:
            messages[-1]["images"] = imgs
        # ⚠ num_predict（出力の上限トークン数）は必ず渡す。既定 2048。
        #   思考モードでは思考が枠を消費するため、足りないと解答が出ず棄権になる。
        opts = {"temperature": temperature,
                "num_predict": int(st.get("num_predict", DEFAULT_NUM_PREDICT))}
        opts["num_ctx"] = int(st.get("num_ctx", DEFAULT_NUM_CTX))
        # ★think の既定は False（2026-08-07・実測で確定）。
        #   指定しないと ollama 側の既定（thinking 系モデルは ON）になり、
        #   **思考が num_predict を食い尽くして本文が空＝全問棄権**になる。
        #   実測(gemma4:26b・同一問題): 未指定 → 解答なし/73秒 ／ think:false → 'a'/19.5秒。
        #   推奨表に載せている gemma4 系（12b / 31b）が同じ挙動なので、既定のままだと
        #   配布版が黙って 0% を出す。⚠上の実測は gemma4:26b で採ったもので、
        #   推奨表のタグ（gemma4:12b / :31b）とは別個体（挙動は同系統）。
        #   thinking を使いたい構成は stage 側で think:true を明示する。
        thv = st.get("think")
        think = True if thv in (True, "true") else False if thv in (False, "false") else False
        try:
            buf = OC.chat(base_url, model, messages, opts, think, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            # 古い ollama は think キー自体を受け付けないことがある。その時だけ従来どおり送る
            if think is False and "think" in str(e).lower():
                buf = OC.chat(base_url, model, messages, opts, None, timeout=timeout)
            else:
                raise
        if stype == "vision_findings":
            ctx["findings"] = buf
        ans = ""
        rec = {"type": stype, "model": model, "answer": ans, "text": buf}
        if stype in _ANSWER_STAGES:
            ans = extract_answer(buf)
            cand.append(ans)
            final = ans
            rec["answer"] = ans
            if not ans:
                # ★黙って「不正解」にしない。解答が取れなかったのは棄権であって誤答ではない。
                #   利用者が原因（思考モード・出力上限・プロンプト）にたどり着けるよう理由を残す。
                rec["note"] = ("解答の形式が読み取れませんでした（棄権）。"
                               "最終行が『解答: a』の形になっていません。"
                               + ("モデルが何も返していません。" if not buf.strip()
                                  else "モデルの出力: " + buf.strip().replace("\n", " ")[:80]))
        stage_answers.append(rec)
    pred = sorted(set(final)) if final else []
    return {"pred": pred, "final": final, "stage_answers": stage_answers,
            "text": stage_answers[-1].get("text", "") if stage_answers else "",
            "note": next((s["note"] for s in reversed(stage_answers) if s.get("note")), "")}
