"""클라이언트 측 데이터 페치 헬퍼.

서버의 /update-data, /update-models 트리거를 호출하고 /progress 폴링으로 완료를 대기한다.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from . import core


def trigger_data_update() -> dict:
    return core.update_server_data()


def trigger_model_update() -> dict:
    return core.update_server_models()


def wait_for_progress(
    kind: str = "data_update",
    poll_interval: float = 2.0,
    timeout: float = 3600.0,
    on_tick: Optional[Callable[[dict], None]] = None,
) -> dict:
    """`kind`(data_update|model_update) 작업이 끝날 때까지 폴링한다."""
    start = time.time()
    while True:
        progress = core._handle_request("get", "/progress")
        slot = progress.get(kind, {}) if isinstance(progress, dict) else {}
        if on_tick:
            on_tick(slot)
        status = slot.get("status")
        if status in {"completed", "error", "idle"} and slot.get("current") == slot.get("total", 0):
            return slot
        if time.time() - start > timeout:
            return {**slot, "status": "timeout"}
        time.sleep(poll_interval)


def fetch_progress() -> dict:
    return core._handle_request("get", "/progress")
