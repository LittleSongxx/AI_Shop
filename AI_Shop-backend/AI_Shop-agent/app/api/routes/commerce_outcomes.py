from fastapi import APIRouter, Depends

from app.api.routes.attribution import require_internal_token
from app.models.commerce_outcome import CommerceOutcomeBatchRequest
from app.models.response import ResponseVO, success
from app.services.commerce_outcome_ledger_service import (
    commerce_outcome_ledger_service,
)

router = APIRouter(prefix="/internal/commerce-outcomes", tags=["internal-commerce-outcomes"])


@router.post("/ingestBatch")
async def ingest_batch(
    body: CommerceOutcomeBatchRequest,
    _internal_token: str = Depends(require_internal_token),
) -> ResponseVO:
    results = await commerce_outcome_ledger_service.record_batch(body.events)
    return success(results)
