from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from server_core.db import db_execute_return_id
from server_core.log import log_event

router = APIRouter(tags=["payment"])


class PaymentApproveBody(BaseModel):
    approval_code: str = Field(..., min_length=1, max_length=32)
    robot_id: str = Field(..., min_length=1, max_length=64)
    amount: Optional[int] = None
    status: str = "APPROVED"
    src: str = "USER_UI"
    note: str = ""


def _insert_payment(body: PaymentApproveBody) -> int:
    return db_execute_return_id(
        """
        INSERT INTO payment_log (
          approval_code,
          robot_id,
          amount,
          status,
          src,
          note,
          created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, NOW(6))
        """,
        (
            body.approval_code.strip(),
            body.robot_id.strip(),
            body.amount,
            (body.status or "APPROVED").strip().upper(),
            (body.src or "USER_UI").strip(),
            (body.note or "")[:255],
        ),
    )


@router.post("/api/payment/approve")
def payment_approve(body: PaymentApproveBody):
    row_id = _insert_payment(body)
    try:
        log_event(
            src="PAYMENT",
            level="INFO",
            event="PAYMENT_APPROVED",
            detail=f"id={row_id} code={body.approval_code} robot_id={body.robot_id}",
            robot_id=body.robot_id,
        )
    except Exception:
        pass
    return {"ok": True, "payment_id": int(row_id or 0)}


@router.post("/payment/approve")
def payment_approve_alias(body: PaymentApproveBody):
    return payment_approve(body)
