from pydantic import BaseModel, Field, field_validator

from app.constants import IMPRESSION_LOG_MAX_PRODUCTS


class AttributionItem(BaseModel):
    requestId: str = Field(min_length=1, max_length=128)
    productId: str = Field(min_length=1, max_length=64)
    position: int = Field(ge=1, le=IMPRESSION_LOG_MAX_PRODUCTS)

    @field_validator("requestId", "productId")
    @classmethod
    def strip_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("identifier cannot be blank")
        return value


class AttributionValidateBatchRequest(BaseModel):
    userId: str = Field(min_length=1, max_length=32)
    items: list[AttributionItem] = Field(default_factory=list, max_length=100)

    @field_validator("userId")
    @classmethod
    def strip_user_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("userId cannot be blank")
        return value
