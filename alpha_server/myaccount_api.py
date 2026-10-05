"""내 실계좌 조회 API. 조회 전용 — 주문 엔드포인트는 없다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .auth import UserPublic, require_user
from .rate_limit import rate_limit

router = APIRouter(prefix="/account", tags=["account"])


@router.get(
    "/overview",
    summary="내 증권사 계좌: 잔고·손익·분석·매매 기록 (조회 전용)",
    dependencies=[Depends(rate_limit("account_overview", capacity=10, per_seconds=60))],
)
def get_overview(broker: str = Query("kb", pattern=r"^(kb)$"), days: int = Query(365, ge=7, le=1825),
                 user: UserPublic = Depends(require_user)):
    from . import myaccount

    return myaccount.clean_for_json(myaccount.overview(user.username, broker, days))
