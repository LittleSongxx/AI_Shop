from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class ShoppingProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str | None = None
    budget_min: Annotated[float | None, Field(alias="budgetMin", ge=0)] = None
    budget_max: Annotated[float | None, Field(alias="budgetMax", ge=0)] = None
    brands: list[str] | None = None
    excluded_brands: Annotated[
        list[str] | None, Field(alias="excludedBrands")
    ] = None
    scenarios: list[str] | None = None
    features: list[str] | None = None
    accept_substitute: Annotated[
        bool | None, Field(alias="acceptSubstitute")
    ] = None


class ShoppingProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: Annotated[int, Field(ge=0)]
    profile: ShoppingProfilePatch


class ShoppingProfileClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: Annotated[int, Field(ge=0)]
