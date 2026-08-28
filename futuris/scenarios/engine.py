"""ScenarioEngine: Perturbations, sensitivity attribution, and comparison."""

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from futuris.core.schemas import Forecast, Scenario
from futuris.scenarios.graph import DependencyGraph
from futuris.scenarios.spec import ScenarioSpec
from futuris.storage.repositories import ScenarioRepository


class AssumptionSensitivity(BaseModel):
    """Measures impact of an assumption override on downstream outputs."""

    assumption_name: str
    override_value: float
    output_variable: str
    absolute_change: float
    relative_percentage_change: float


class ScenarioResult(BaseModel):
    """Result of running a single scenario simulation."""

    scenario_id: UUID = Field(default_factory=uuid4)
    spec: ScenarioSpec
    parent_forecast_id: UUID
    perturbed_values: dict[str, float]  # Node -> mean point estimate
    distributions: dict[str, dict[str, float]] = Field(default_factory=dict)
    sensitivity_ranking: list[AssumptionSensitivity] = Field(default_factory=list)


class ScenarioComparison(BaseModel):
    """Side-by-side comparison across multiple scenarios."""

    parent_forecast_id: UUID
    scenario_names: list[str]
    variable_matrix: dict[str, dict[str, float]]  # variable -> {scenario_name: value}
    divergence_ranking: list[tuple[str, float]]  # (variable, max_divergence_pct)
    top_drivers: list[str]


class ScenarioEngine:
    """Simulates counterfactual scenarios and compares diverging futures."""

    def __init__(self, scenario_repo: ScenarioRepository | None = None) -> None:
        self.scenario_repo = scenario_repo

    async def run_scenario(
        self,
        base_forecast: Forecast,
        spec: ScenarioSpec,
        graph: DependencyGraph | None = None,
        use_monte_carlo: bool = True,
        num_samples: int = 1000,
    ) -> ScenarioResult:
        """Simulate a scenario without mutating base forecast or evidence snapshots."""
        base_demand = float(base_forecast.prediction)
        sim_graph = graph or DependencyGraph.default_ops_wedge(base_demand=base_demand)

        # 1. Propagate values
        if use_monte_carlo:
            dist_results = sim_graph.propagate_monte_carlo(
                spec.assumption_overrides, num_samples=num_samples
            )
            perturbed_vals = {node: stats["mean"] for node, stats in dist_results.items()}
        else:
            perturbed_vals = sim_graph.propagate_linear(spec.assumption_overrides)
            dist_results = {
                node: {"mean": val, "std": 0.0, "p10": val, "p50": val, "p90": val}
                for node, val in perturbed_vals.items()
            }

        # 2. Compute Sensitivity Ranking
        sensitivities: list[AssumptionSensitivity] = []
        for var_name, override_val in spec.assumption_overrides.items():
            single_override = {var_name: override_val}
            single_res = sim_graph.propagate_linear(single_override)
            for out_var, new_val in single_res.items():
                base_val = sim_graph.base_values.get(out_var, 1.0)
                abs_change = abs(new_val - base_val)
                pct_change = (abs_change / (base_val + 1e-5)) * 100.0
                sensitivities.append(
                    AssumptionSensitivity(
                        assumption_name=var_name,
                        override_value=override_val,
                        output_variable=out_var,
                        absolute_change=round(abs_change, 2),
                        relative_percentage_change=round(pct_change, 2),
                    )
                )

        sensitivities.sort(key=lambda s: s.relative_percentage_change, reverse=True)

        scenario_id = uuid4()
        result = ScenarioResult(
            scenario_id=scenario_id,
            spec=spec,
            parent_forecast_id=base_forecast.forecast_id,
            perturbed_values=perturbed_vals,
            distributions=dist_results,
            sensitivity_ranking=sensitivities[:10],
        )

        # 3. Persist Scenario Domain Record if repository is provided
        if self.scenario_repo:
            scenario_record = Scenario(
                scenario_id=scenario_id,
                name=spec.name,
                scenario_type=spec.scenario_type,
                assumptions_override=spec.assumption_overrides,
                created_by=spec.created_by or "system",
                parent_forecast_id=base_forecast.forecast_id,
            )
            await self.scenario_repo.create(scenario_record)

        return result

    def compare(
        self,
        base_forecast: Forecast,
        results: list[ScenarioResult],
    ) -> ScenarioComparison:
        """Compare multiple scenario outcomes side-by-side with divergence ranking."""
        scenario_names = [r.spec.name for r in results]
        all_variables = list(results[0].perturbed_values.keys()) if results else []

        matrix: dict[str, dict[str, float]] = {v: {} for v in all_variables}
        divergences: list[tuple[str, float]] = []

        for v in all_variables:
            values = [r.perturbed_values.get(v, 0.0) for r in results]
            for r in results:
                matrix[v][r.spec.name] = r.perturbed_values.get(v, 0.0)

            if values:
                min_v, max_v = min(values), max(values)
                mean_v = sum(values) / len(values)
                div_pct = ((max_v - min_v) / (mean_v + 1e-5)) * 100.0
                divergences.append((v, round(div_pct, 2)))

        divergences.sort(key=lambda x: x[1], reverse=True)

        top_drivers_set = []
        for r in results:
            for s in r.sensitivity_ranking[:3]:
                if s.assumption_name not in top_drivers_set:
                    top_drivers_set.append(s.assumption_name)

        return ScenarioComparison(
            parent_forecast_id=base_forecast.forecast_id,
            scenario_names=scenario_names,
            variable_matrix=matrix,
            divergence_ranking=divergences,
            top_drivers=top_drivers_set,
        )
