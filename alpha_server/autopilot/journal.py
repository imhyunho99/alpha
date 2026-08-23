"""운용 이벤트 기록. 감사 로그에도 함께 남긴다."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class Journal:
    def __init__(self, actor: str = "autopilot", mirror_audit: bool = True) -> None:
        self.events: list[dict[str, Any]] = []
        self._actor = actor
        self._mirror = mirror_audit

    def record(self, kind: str, **fields: Any) -> dict[str, Any]:
        at = fields.pop("at", None)
        stamp = at.isoformat() if isinstance(at, datetime) else datetime.now(timezone.utc).isoformat()

        event = {"kind": kind, "at": stamp, **fields}
        self.events.append(event)

        if self._mirror:
            try:
                from .. import audit_log

                audit_log.record("trade", f"autopilot_{kind}", actor=self._actor, **fields)
            except Exception:
                pass  # 감사 로그 실패가 운용을 막지는 않는다
        return event
