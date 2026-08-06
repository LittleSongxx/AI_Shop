from fastapi import APIRouter, Depends, Header, HTTPException

from app.config.settings import get_settings
from app.models.attribution import AttributionValidateBatchRequest
from app.models.response import ResponseVO, success
from app.services.recommendation_attribution_service import (
    recommendation_attribution_service,
)

router = APIRouter(prefix="/internal/attribution", tags=["internal-attribution"])


def require_internal_token(
    x_internal_token: str | None = Header(None, alias="X-Internal-Token"),
) -> str:
    if not x_internal_token or x_internal_token != get_settings().internal_token:
        raise HTTPException(status_code=401, detail="invalid internal token")
    return x_internal_token


@router.post("/validateBatch")
async def validate_batch(
    body: AttributionValidateBatchRequest,
    _internal_token: str = Depends(require_internal_token),
) -> ResponseVO:
    rows = await recommendation_attribution_service.validate_batch(
        body.userId,
        [item.model_dump() for item in body.items],
    )
    return success(rows)
