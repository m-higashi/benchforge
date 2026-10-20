"""ランナーバックエンド抽象化（配布/独立版に向けたスキャフォールド・2026-07-18）。

夜間ラン/ラン制御は現状 Linux+systemd+radbench 専用（app/services/runner.py）。
将来 自己完結した配布アプリにするため、実行系を差し替え可能な RunnerBackend で抽象化する。

- RadbenchSystemdBackend : 現行の runner.py に委譲するだけ（挙動不変・既存の夜間ランタブはこれ相当）。
- StandaloneRunnerBackend: ★スタブ。radbench 非依存で データセット の IN-BOX を読み、
  results/trials.jsonl を書くまでの「配線」を通す。**実際にモデルで解く _solve だけが未実装（GPU要）**。
  ＝ここを埋めれば配布版が自己完結する、という差し込み点を明示するのが目的。

このモジュールは既存 runner.py を import して委譲するだけで、現行の夜間ランタブは無改変。
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from app.services import runner as RUN
from app.services import standalone as SA


class RunnerBackend(ABC):
    """ラン実行系の共通インタフェース。OS/実行基盤に依存する部分をここに閉じ込める。"""

    name: str = "abstract"

    @abstractmethod
    def apply(self, plan: dict, dataset: str = "") -> dict: ...
    @abstractmethod
    def start(self) -> dict: ...
    @abstractmethod
    def stop(self) -> dict: ...
    @abstractmethod
    def pause(self) -> dict: ...
    @abstractmethod
    def resume(self) -> dict: ...
    @abstractmethod
    def set_timer(self, oncalendar: str) -> dict: ...
    @abstractmethod
    def clear_timer(self) -> dict: ...
    @abstractmethod
    def status(self) -> dict: ...


class RadbenchSystemdBackend(RunnerBackend):
    """現行実装：radbench の plans/tonight_plan.json ＋ systemd(radbench-trial) を操作。
    Linux+systemd 専用。既存 runner.py にそのまま委譲（挙動不変）。"""

    name = "radbench+systemd (Linux)"

    def __init__(self, results_dir: Path):
        self.results_dir = results_dir

    def apply(self, plan: dict, dataset: str = "") -> dict:
        return RUN.apply_plan(self.results_dir, plan)   # dataset は外部実行系では使わない
    def start(self) -> dict: return RUN.start(self.results_dir)
    def stop(self) -> dict: return RUN.stop(self.results_dir)
    def pause(self) -> dict: return RUN.pause(self.results_dir)
    def resume(self) -> dict: return RUN.resume(self.results_dir)
    def set_timer(self, oncalendar: str) -> dict: return RUN.set_timer(self.results_dir, oncalendar)
    def clear_timer(self) -> dict: return RUN.clear_timer(self.results_dir)
    def status(self) -> dict: return {"backend": self.name, **RUN.status(self.results_dir)}


class StandaloneRunnerBackend(RunnerBackend):
    """★配布/独立版スタブ：radbench 非依存。データセット の IN-BOX を読み、
    results/trials.jsonl を書くまでの配線を通す。OS依存なし（systemd不使用）。

    未実装（要GPU）＝ `_solve()` のみ。ここに「自前 ollama で1問解く」処理を入れれば
    配布版が自己完結する。それ以外（IN-BOX読込→採点→trials.jsonl書込→統計へ）は完成。
    """

    name = "standalone (stub・OS非依存)"

    def __init__(self, dataset_dir: Path | None = None, ollama_url: str | None = None,
                 prompts: dict | None = None):
        self.ds = dataset_dir or SA.dataset_dir()
        self.ollama_url = ollama_url
        self.prompts = prompts or {}
        self.state_path = SA.APP_ROOT / "data" / "standalone_run.json"

    # ── 状態（systemdの代わりに単純なJSONで持つ）──
    def _load_state(self) -> dict:
        if self.state_path.is_file():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {"plan": None, "state": "idle", "paused": False, "timer": None, "last_run": None}

    def _save_state(self, st: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")

    def apply(self, plan: dict, dataset: str = "") -> dict:
        st = self._load_state(); st["plan"] = plan; st["state"] = "applied"
        # ★どのデータセットに対して実行するかを状態に残す。起動は別のHTTP要求なので、
        #   ここで覚えておかないと「画面で選んだデータセット」が起動時に失われる。
        st["dataset"] = dataset or self.ds.name
        self._save_state(st)
        return {"ok": True, "backend": self.name, "plan": plan, "dataset": st["dataset"]}

    # ── 本体（自前 ollama で1問解く・GPU要）──
    def _solve(self, qid: str, stages: list[dict]) -> dict:
        """IN-BOX の問題(本文＋画像)を読み、構成のステージを自前 ollama で実行する。
        返り値は solve_question の結果そのまま（pred だけでなく生出力 text と棄権理由 note も使う）。"""
        from app.services import solver as SV
        from app.services import question_view as QV
        if not self.ollama_url:
            raise RuntimeError("ollama_url 未指定（自前 ollama の場所が不明）")
        q = QV.get_question(qid, self.ds.name, None)   # 自前データは IN-BOX から
        if q is None:
            return {"pred": [], "text": "", "note": f"問題 {qid} を読み込めませんでした"}
        return SV.solve_question(self.ollama_url, self.prompts, stages,
                                 question=q.get("raw") or q.get("stem", ""),
                                 captions="\n".join(q.get("captions") or []),
                                 images_b64=q.get("images") or [], temperature=0.0)

    def _stub_solve(self, qid: str, gold: list[str]) -> dict:
        """配線確認用のダミー解答＝常に選択肢 a（明らかにスタブと分かる素朴ベースライン）。"""
        return {"pred": ["a"], "text": "", "note": ""}

    def run(self, config_id: str = "stub_runner", use_stub: bool = True,
            stages: list[dict] | None = None) -> dict:
        """IN-BOX の全問を解いて results/trials.jsonl に書く。
        use_stub=True: _stub_solve（GPU不要・配線確認）。False: _solve（自前 ollama・GPU要・stages必須）。"""
        answers = SA.load_answers(self.ds)
        qids = SA.list_questions(self.ds)
        out_fp = self.ds / "results" / "trials.jsonl"
        out_fp.parent.mkdir(parents=True, exist_ok=True)
        # ★run_id を必ず付ける（2026-08-07）。無いと成績一覧のドリルダウンが
        #   (run_id, config_id) で引けず、**クリックしても中身が出ない**（実測で確認）。
        run_id = datetime.now().strftime("%Y-%m-%dT%H%M%S")
        # 既存の同 (run_id, config_id) 行を除いて（冪等）残りを保持
        kept = []
        if out_fp.is_file():
            for line in out_fp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not (r.get("config_id") == config_id and (r.get("run_id") or "") == run_id):
                    kept.append(r)
        # ★構成にモデルが書かれていなければ、入っているモデルで埋める（黙って0問にしない）。
        #   何を採用したかは必ずログに残す＝「何で測った結果か」が後から分かるように。
        auto_tag = ""
        if not use_stub and stages:
            from app.services import solver as SV
            stages, auto_tag = SV.fill_missing_models(self.ollama_url, stages)
        new_rows, raw_rows = [], []
        self._log(f"開始 run_id={run_id} config={config_id} 問題数={len(qids)}"
                  + ("（配線確認スタブ）" if use_stub else ""))
        if auto_tag:
            self._log(f"構成にモデルが設定されていないため、入っている {auto_tag} を使います"
                      f"（『構成の作成』で指定すると、この自動選択は行われません）")
        for i, qid in enumerate(qids, 1):
            gold = answers.get(qid, [])
            t0 = time.time()
            res = self._stub_solve(qid, gold) if use_stub else self._solve(qid, stages or [])
            pred = res.get("pred") or []
            note = "stub (配線確認・未解答)" if use_stub else (res.get("note") or None)
            new_rows.append({
                "run_id": run_id,
                "config_id": config_id, "q_id": qid, "pred": sorted(pred),
                "gold": sorted(gold), "correct": (sorted(pred) == sorted(gold)),
                "latency_sec": round(time.time() - t0, 3),
                "abstained": not pred,     # 棄権と誤答を区別する（正答率の解釈が変わる）
                "note": note,
                # 何の設定で得た結果かを残す（成績表に出し、比較の前提を確認できるように）
                **self._param_record(stages or []),
            })
            # 列名は question_id。読み取り側(results_reader.raw_outputs)がこの名前を見る
            raw_rows.append({"question_id": qid, "answer": "".join(sorted(pred)),
                             "raw_output": res.get("text") or ""})
            # ★棄権を「不正解」と呼ばない（本プロジェクトの原則＝棄権は誤答ではない）。
            #   さらに、理由は trials.jsonl にしか無く**利用者が見るのはこのログ**なので、
            #   いちばん役に立つ一文が最も見られない所にあった（Win 実機検証 2026-08-14）。
            verdict = ("棄権" if not pred
                       else "正解" if sorted(pred) == sorted(gold) else "不正解")
            self._log(f"[{i}/{len(qids)}] {qid} 解答={''.join(sorted(pred)) or '（取れず）'} "
                      f"{verdict} {round(time.time() - t0, 1)}秒"
                      + (f"  ← {note}" if not pred and note else ""))
        # ★newline="" は必須。ここは追記でなく全書き換え（kept + new_rows）なので、
        #   指定しないと Windows では **元からあった行まで CRLF に変わる**。
        #   同梱サンプルは git 管理下にあり .gitattributes が eol=lf なので、
        #   実行しただけで作業ツリーが汚れ、次の git pull で衝突する（Win 実機検証 2026-08-10）。
        with open(out_fp, "w", encoding="utf-8", newline="") as f:
            for r in kept + new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        self._write_raw(run_id, config_id, raw_rows)
        n_abst = sum(1 for r in new_rows if r["abstained"])
        self._log(f"完了 run_id={run_id} 解答={len(new_rows)}問 棄権={n_abst}問"
                  + ("  ⚠ 全問で解答が取れませんでした。構成の think 設定と出力上限を確認してください"
                     if n_abst == len(new_rows) and new_rows and not use_stub else ""))
        self._log("idle")
        st = self._load_state(); st["state"] = "done"; st["last_run"] = {
            "run_id": run_id, "config_id": config_id, "n": len(new_rows),
            "abstained": n_abst, "stub": use_stub}
        self._save_state(st)
        return {"ok": True, "backend": self.name, "config_id": config_id, "run_id": run_id,
                "solved": len(new_rows), "abstained": n_abst, "stub": use_stub,
                "note": "配線確認用スタブ。実解答は _solve 実装(GPU)で置換。" if use_stub else "本実装",
                "results_file": str(out_fp)}

    def _log(self, msg: str) -> None:
        """results/run_trial.log に進行を書く。
        ★「実行状況の監視」タブはこのファイルだけを見ている。standalone が書いていなかったため、
          配布版では**必ず『run_trial.log が見つかりません』**になっていた（2026-08-07 修正）。"""
        try:
            fp = self.ds / "results" / "run_trial.log"
            fp.parent.mkdir(parents=True, exist_ok=True)
            with open(fp, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
        except OSError:
            pass   # 監視ログは補助。書けなくても解答そのものは続ける

    def _write_raw(self, run_id: str, config_id: str, rows: list[dict]) -> None:
        """results/raw/<run_id>/<config_id>.csv に生出力を残す。
        成績一覧・比較タブのドリルダウンは、この生出力を読んで「モデルが何と答えたか」を見せる。"""
        import csv
        try:
            d = self.ds / "results" / "raw" / run_id
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f"{config_id}.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["question_id", "answer", "raw_output"])
                w.writeheader()
                w.writerows(rows)
        except OSError:
            pass

    @staticmethod
    def _param_record(stages: list[dict]) -> dict:
        """構成で実際に使った生成パラメータを1行分にまとめる。
        ステージごとに違う場合はカンマ区切りで併記する（例 "2048,6144"）。"""
        from app.services import solver as SV

        def collect(key, default):
            vals = []
            for st in stages:
                if not st.get("model"):
                    continue
                v = st.get(key, default)
                if v not in vals:
                    vals.append(v)
            return ",".join(str(v) for v in vals) if vals else None

        return {
            "num_predict": collect("num_predict", SV.DEFAULT_NUM_PREDICT),
            "num_ctx": collect("num_ctx", SV.DEFAULT_NUM_CTX),
            "backend": "ollama",
        }

    # ── OS非依存の制御（systemd不使用・背景スレッドで実行）──
    def start(self) -> dict:
        """適用プランの先頭 config を背景スレッドで実行（非ブロッキング）。
        ollama_url＋stages があれば自前 ollama で実解答、無ければ配線確認スタブ。"""
        import threading
        st = self._load_state()
        if st.get("state") == "running":
            return {"ok": False, "backend": self.name, "msg": "すでに実行中です"}
        plan = st.get("plan") or {}
        stages, cfgid = None, "run"
        if plan.get("configs"):
            c0 = plan["configs"][0]
            stages = c0.get("stages"); cfgid = c0.get("config_id") or "run"
        use_stub = not (self.ollama_url and stages)
        st["state"] = "running"; st["running_config"] = cfgid; st.pop("error", None)
        self._save_state(st)

        def _bg():
            try:
                self.run(config_id=cfgid, use_stub=use_stub, stages=stages)
            except Exception as e:  # noqa: BLE001
                s = self._load_state(); s["state"] = "error"; s["error"] = str(e)[:200]
                self._save_state(s)
        threading.Thread(target=_bg, daemon=True).start()
        return {"ok": True, "backend": self.name, "started": cfgid, "stub": use_stub,
                "msg": f"バックグラウンドで実行開始（{'配線確認' if use_stub else '自前 ollama で実解答'}）"}

    def stop(self) -> dict:
        st = self._load_state(); st["state"] = "idle"; self._save_state(st)
        return {"ok": True, "backend": self.name, "state": "idle"}

    def pause(self) -> dict:
        st = self._load_state(); st["paused"] = True; self._save_state(st)
        return {"ok": True, "backend": self.name, "paused": True}

    def resume(self) -> dict:
        st = self._load_state(); st["paused"] = False; self._save_state(st)
        return {"ok": True, "backend": self.name, "paused": False}

    def set_timer(self, oncalendar: str) -> dict:
        st = self._load_state(); st["timer"] = oncalendar; self._save_state(st)
        return {"ok": True, "backend": self.name, "timer": oncalendar,
                "note": "スタブ＝スケジュール文字列を記録のみ。配布版でOS非依存スケジューラに接続する差し込み点。"}

    def clear_timer(self) -> dict:
        st = self._load_state(); st["timer"] = None; self._save_state(st)
        return {"ok": True, "backend": self.name, "timer": None}

    def status(self) -> dict:
        st = self._load_state()
        # 夜間ランタブUI(radbench形)互換のキーも付す
        compat = {
            "service": "active" if st.get("state") == "running" else "idle",
            "paused": bool(st.get("paused")),
            "timer_enabled": "enabled" if st.get("timer") else "disabled",
            "plan_exists": bool(st.get("plan")),
        }
        return {"backend": self.name, **st, **compat}
