import re
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import Machine

router = APIRouter(prefix="/api/machines", tags=["machines"])

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

_IPV4_RE = re.compile(
    r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$"
)


class MachineCreate(BaseModel):
    name: str
    ip: str

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        m = _IPV4_RE.match(v.strip())
        if not m:
            raise ValueError("Invalid IPv4 address format")
        for octet in m.groups():
            if int(octet) > 255:
                raise ValueError(f"Octet {octet} out of range (0-255)")
        return v.strip()

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Machine name must not be empty")
        return v


class MachineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ip: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/", response_model=List[MachineOut])
async def list_machines(
    db: Session = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    return db.query(Machine).order_by(Machine.id).all()


@router.post("/", response_model=MachineOut, status_code=201)
async def create_machine(
    machine_in: MachineCreate,
    db: Session = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    machine = Machine(name=machine_in.name, ip=machine_in.ip)
    db.add(machine)
    db.commit()
    db.refresh(machine)
    return machine


@router.put("/{machine_id}", response_model=MachineOut)
async def update_machine(
    machine_id: int,
    machine_in: MachineCreate,
    db: Session = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    machine = db.get(Machine, machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    machine.name = machine_in.name
    machine.ip = machine_in.ip
    db.commit()
    db.refresh(machine)
    return machine


@router.delete("/{machine_id}", status_code=204)
async def delete_machine(
    machine_id: int,
    db: Session = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    machine = db.get(Machine, machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    db.delete(machine)
    db.commit()
