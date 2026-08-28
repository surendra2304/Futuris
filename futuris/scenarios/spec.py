"""Scenario specification models and builder helpers for counterfactual and stress simulations."""

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from futuris.core.enums import ScenarioType


class ScenarioSpecInput(BaseModel):
    """API-facing input payload for constructing a ScenarioSpec."""

    scenario_type: ScenarioType = Field(default=ScenarioType.USER_DEFINED)
    name: str
    assumption_overrides: dict[str, float] = Field(default_factory=dict)
    rationale: str = ""
    created_by: str = "system"


class ScenarioSpec(BaseModel):
    """Domain specification defining counterfactual interventions and assumption perturbations."""

    spec_id: UUID = Field(default_factory=uuid4)
    scenario_type: ScenarioType
    name: str
    assumption_overrides: dict[str, float] = Field(default_factory=dict)
    rationale: str
    created_by: str = "system"

    @classmethod
    def baseline(cls, name: str = "Baseline Scenario") -> "ScenarioSpec":
        """Construct a baseline scenario with zero parameter overrides."""
        return cls(
            scenario_type=ScenarioType.BASELINE,
            name=name,
            assumption_overrides={},
            rationale="Unperturbed baseline projection assuming status quo.",
        )

    @classmethod
    def upside(
        cls,
        demand_multiplier: float = 1.15,
        capacity: float | None = None,
        name: str = "Upside Scenario",
    ) -> "ScenarioSpec":
        """Construct an upside growth scenario (higher demand, sustained or increased capacity)."""
        overrides = {"demand": demand_multiplier}
        if capacity is not None:
            overrides["capacity"] = capacity
        return cls(
            scenario_type=ScenarioType.UPSIDE,
            name=name,
            assumption_overrides=overrides,
            rationale=f"Upside demand expansion (+{(demand_multiplier - 1.0)*100:.1f}%).",
        )

    @classmethod
    def downside(
        cls,
        demand_multiplier: float = 0.85,
        capacity: float | None = None,
        name: str = "Downside Scenario",
    ) -> "ScenarioSpec":
        """Construct a downside contraction scenario (reduced demand)."""
        overrides = {"demand": demand_multiplier}
        if capacity is not None:
            overrides["capacity"] = capacity
        return cls(
            scenario_type=ScenarioType.DOWNSIDE,
            name=name,
            assumption_overrides=overrides,
            rationale=f"Downside contraction ({(demand_multiplier - 1.0)*100:.1f}%).",
        )

    @classmethod
    def stress(
        cls,
        demand_multiplier: float = 1.40,
        capacity_multiplier: float = 0.80,
        name: str = "Stress Scenario",
    ) -> "ScenarioSpec":
        """Construct a severe stress test scenario (e.g. demand +40%, capacity -20%)."""
        return cls(
            scenario_type=ScenarioType.STRESS,
            name=name,
            assumption_overrides={
                "demand": demand_multiplier,
                "capacity": capacity_multiplier,
            },
            rationale=(
                f"Severe operational stress test: demand "
                f"+{(demand_multiplier - 1.0)*100:.0f}%, capacity "
                f"{(capacity_multiplier - 1.0)*100:.0f}%."
            ),
        )

    @classmethod
    def counterfactual(
        cls,
        condition: str,
        overrides: dict[str, float],
        name: str = "Counterfactual Scenario",
    ) -> "ScenarioSpec":
        """Construct a what-if counterfactual scenario with specific overrides."""
        return cls(
            scenario_type=ScenarioType.COUNTERFACTUAL,
            name=name,
            assumption_overrides=overrides,
            rationale=f"Counterfactual condition: {condition}",
        )

    @classmethod
    def user_defined(
        cls,
        overrides: dict[str, float],
        name: str = "Custom Scenario",
        rationale: str = "User customized overrides",
        created_by: str = "user",
    ) -> "ScenarioSpec":
        """Construct a user-defined scenario."""
        return cls(
            scenario_type=ScenarioType.USER_DEFINED,
            name=name,
            assumption_overrides=overrides,
            rationale=rationale,
            created_by=created_by,
        )
