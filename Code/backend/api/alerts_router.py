from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.dependencies import get_session
from backend.api.repository import AlertRepository
from backend.models.schemas import AlertOut

router = APIRouter(tags=["alerts"])


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(session: Session = Depends(get_session)) -> list[AlertOut]:
    alerts = AlertRepository(session).list_all()
    return [AlertOut.model_validate(a) for a in alerts]


@router.get("/alerts/active", response_model=list[AlertOut])
def list_active_alerts(session: Session = Depends(get_session)) -> list[AlertOut]:
    alerts = AlertRepository(session).list_active()
    return [AlertOut.model_validate(a) for a in alerts]
