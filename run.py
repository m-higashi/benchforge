#!/usr/bin/env python3
"""Benchforge 起動エントリ。config.yaml の host/port（環境変数で上書き可）で uvicorn 起動。"""
from __future__ import annotations

import logging

import uvicorn

from app.config import load_config

if __name__ == "__main__":
    cfg = load_config()
    srv = cfg.get("server", {})
    host = srv.get("host", "0.0.0.0")
    port = int(srv.get("port", 8095))
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/"
    print("=" * 52, flush=True)
    print("  Benchforge を起動しました", flush=True)
    print(f"  ブラウザで {url} を開いてください", flush=True)
    print("  停止するには、このウィンドウを閉じるか Ctrl+C を押してください", flush=True)
    print("=" * 52, flush=True)

    # ★画面操作のたびに出る "GET /api/... 200 OK" を止める（2026-08-07）。
    #   1回タブを開くだけで十数行流れ、**本当に見せたいエラーが押し流されていた**。
    #   消すのはアクセスログだけで、警告・エラーは残す（level=WARNING）。
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    uvicorn.run("app.main:app", host=host, port=port,
                log_level="warning", access_log=False)
