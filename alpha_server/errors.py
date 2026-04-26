"""구조화된 에러 응답 + 글로벌 예외 핸들러."""
from __future__ import annotations

import logging
import traceback
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("alpha")


def _payload(request_id: str, code: str, detail: str, **extra) -> dict:
    body = {"error": {"code": code, "detail": detail, "request_id": request_id}}
    if extra:
        body["error"].update(extra)
    return body


def install_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:  # 핸들러에서 처리되지 않은 모든 예외
            raise
        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        rid = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(rid, f"http_{exc.status_code}", str(exc.detail)),
            headers=exc.headers or {},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "unknown")
        logger.error("[%s] unhandled: %s\n%s", rid, exc, traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content=_payload(rid, "internal_error", "서버에서 예기치 못한 오류가 발생했습니다."),
        )
