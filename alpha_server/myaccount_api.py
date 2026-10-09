"""내 실계좌 조회 API. 조회 전용 — 주문 엔드포인트는 없다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

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


class ShadowPayload(BaseModel):
    broker: str = Field("kb", pattern=r"^(kb)$")
    temperature: int = Field(5, ge=1, le=7)   # 8 이상은 모의 레버리지 — 실계좌 복제에는 쓰지 않는다
    sleeve: bool = True                        # 새 돈만 운용(기존 보유 종목은 건드리지 않음)


@router.get("/shadow", summary="실계좌 리밸런싱(기록만) 상태와 기록된 주문")
def get_shadow(user: UserPublic = Depends(require_user)):
    from . import myaccount

    return myaccount.clean_for_json(myaccount.shadow_status(user.username))


@router.post(
    "/shadow",
    summary="실계좌 잔고로 에이전트 시작/다시 맞추기 — 주문은 기록만",
    dependencies=[Depends(rate_limit("account_shadow", capacity=3, per_seconds=60))],
)
def post_shadow(payload: ShadowPayload, user: UserPublic = Depends(require_user)):
    from fastapi import HTTPException

    from . import myaccount
    from .newsdesk import runner

    try:
        status = myaccount.seed_shadow(user.username, payload.broker, payload.temperature, sleeve=payload.sleeve)
    except ValueError:
        raise HTTPException(status_code=400, detail="KB증권 API 키가 등록되지 않았습니다.")
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"증권사 잔고를 읽지 못했습니다: {exc}")
    runner.start(user.username, myaccount.SHADOW_PORTFOLIO)
    return myaccount.clean_for_json(status)


@router.delete("/shadow", summary="실계좌 리밸런싱 멈추기")
def delete_shadow(user: UserPublic = Depends(require_user)):
    from . import myaccount
    from .newsdesk import runner

    runner.stop(user.username, myaccount.SHADOW_PORTFOLIO)
    return myaccount.clean_for_json(myaccount.stop_shadow(user.username))
