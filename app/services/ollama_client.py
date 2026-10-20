"""Benchforge 自身の ollama クライアント（radbench 非経由）。P2 ではモデル一覧のみ。"""
from __future__ import annotations

import urllib.request
import json


def stream_chat(base_url: str, model: str, messages: list[dict],
                options: dict | None = None, think=None, timeout: float = 900.0):
    """ollama /api/chat を stream で叩き、{thinking, content, done} の delta を逐次 yield。
    thinking 対応モデルは message.thinking、本文は message.content（どちらも増分）。"""
    payload = {"model": model, "messages": messages, "stream": True,
               "options": options or {}}
    if think is not None:
        payload["think"] = think
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg = d.get("message") or {}
            yield {"thinking": msg.get("thinking") or "",
                   "content": msg.get("content") or "",
                   "done": bool(d.get("done"))}
            if d.get("done"):
                break


def chat(base_url: str, model: str, messages: list[dict],
         options: dict | None = None, think=None, timeout: float = 120.0) -> str:
    """非ストリームの ollama /api/chat。本文(content)を返す（翻訳など短い用途）。"""
    payload = {"model": model, "messages": messages, "stream": False, "options": options or {}}
    if think is not None:
        payload["think"] = think
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (d.get("message") or {}).get("content", "") or ""


def list_models(base_url: str, timeout: float = 5.0) -> list[dict]:
    """ollama /api/tags からモデル一覧を返す。失敗時は空リスト。
    返り値: [{"name": "gemma4:31b", "size_gb": 19.0, "family": "...", "params": "..."}]
    """
    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for m in data.get("models", []):
        det = m.get("details", {}) or {}
        out.append({
            "name": m.get("name"),
            "size_gb": round((m.get("size") or 0) / 1e9, 1),
            "family": det.get("family"),
            "params": det.get("parameter_size"),
            "quant": det.get("quantization_level"),
        })
    out.sort(key=lambda x: x.get("name") or "")
    return out
