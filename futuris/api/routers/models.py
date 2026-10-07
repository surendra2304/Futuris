"""Model registry and persisted benchmark scoring router."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.deps import get_db_session
from futuris.infra.auth import AllowAnonymousRead
from futuris.models.registry import model_registry
from futuris.storage.repositories import EvaluationRepository

router = APIRouter(prefix="/v1/models", tags=["Models"])


class ModelRegistryItem(BaseModel):
    """Model adapter metadata and measured benchmark scores.

    ``benchmark_status`` distinguishes a model whose scores were measured by a
    persisted evaluation run ("measured") from one that has never been
    benchmarked ("not_benchmarked"). Scores are never invented for the latter.
    """

    name: str
    version_hash: str
    is_active: bool
    family: str
    benchmark_status: str
    benchmark_scores: dict[str, float]


@router.get("", response_model=list[ModelRegistryItem], summary="List Registered Models")
async def list_models(
    user: AllowAnonymousRead,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ModelRegistryItem]:
    """List registered model adapters with their persisted benchmark scores."""
    _ = user
    eval_repo = EvaluationRepository(session)
    models: list[ModelRegistryItem] = []

    for name in model_registry.list_models():
        adapter = model_registry.get_adapter(name)
        ver_str = model_registry.get_version_string(adapter)
        ver_hash = ver_str.split(":")[-1] if ":" in ver_str else "unversioned"

        latest = await eval_repo.latest_for_model(ver_str)
        scores: dict[str, float] = {}
        if latest and isinstance(latest.get("metrics"), dict):
            scores = {
                str(k): float(v)
                for k, v in latest["metrics"].items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }

        models.append(
            ModelRegistryItem(
                name=name,
                version_hash=ver_hash,
                is_active=True,
                family=name,
                benchmark_status="measured" if scores else "not_benchmarked",
                benchmark_scores=scores,
            )
        )

    return models
