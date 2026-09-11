from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.dependencies import get_session
from backend.api.repository import DeviceRepository
from backend.models.schemas import DeviceOut

router = APIRouter(tags=["devices"])


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(session: Session = Depends(get_session)) -> list[DeviceOut]:
    devices = DeviceRepository(session).list_all()
    return [DeviceOut.model_validate(d) for d in devices]


@router.get("/devices/{device_id}", response_model=DeviceOut)
def get_device(device_id: str, session: Session = Depends(get_session)) -> DeviceOut:
    device = DeviceRepository(session).get_by_device_id(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    return DeviceOut.model_validate(device)
