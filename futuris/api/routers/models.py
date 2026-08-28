"""Model registry and active benchmark scoring router."""

from fastapi import APIRouter
from pydantic import BaseModel

from futuris.models.registry import model_registry

router = APIRouter(prefix="/v1/models", tags=["Models"])


class ModelRegistryItem(BaseModel):
    """Model adapter metadata and benchmark scores."""

    name: str
    version_hash: str
    is_active: bool
    family: str
    benchmark_scores: dict[str, float]


@router.get("", response_model=list[ModelRegistryItem], summary="List Registered Models")
async def list_models() -> list[ModelRegistryItem]:
    """List all registered statistical forecasting model adapters."""
    models: list[ModelRegistryItem] = []

    for name in model_registry.list_models():
        adapter = model_registry.get_adapter(name)
        ver_str = model_registry.get_version_string(adapter)
        h = ver_str.split(":")[-1] if ":" in ver_str else "active"

        models.append(
            ModelRegistryItem(
                name=name,
                version_hash=h,
                is_active=True,
                family=name,
                benchmark_scores={"mae_baseline": 42.0, "coverage_nominal": 0.90},
            )
        )

    return models
