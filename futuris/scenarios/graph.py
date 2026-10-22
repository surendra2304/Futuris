"""DependencyGraph: Causal variable relationships and Monte Carlo propagation."""

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Edge:
    """Directed influence link from source node to target node with elasticity."""

    source: str
    target: str
    sensitivity: float  # Elasticity coefficient: d(target_pct) / d(source_pct)


@dataclass
class DependencyGraph:
    """Causal DAG of variables supporting linear sensitivity and Monte Carlo propagation."""

    nodes: list[str] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    base_values: dict[str, float] = field(default_factory=dict)

    @classmethod
    def default_ops_wedge(
        cls,
        base_demand: float = 3500.0,
        base_capacity: float = 4000.0,
    ) -> "DependencyGraph":
        """Construct standard operational capacity forecasting DAG."""
        nodes = ["demand", "capacity", "utilization", "latency", "error_rate", "revenue"]
        edges = [
            Edge(source="demand", target="utilization", sensitivity=1.0),
            Edge(source="capacity", target="utilization", sensitivity=-1.0),
            Edge(source="utilization", target="latency", sensitivity=2.5),
            Edge(source="utilization", target="error_rate", sensitivity=3.0),
            Edge(source="demand", target="revenue", sensitivity=1.0),
            Edge(source="error_rate", target="revenue", sensitivity=-0.5),
        ]
        base_values = {
            "demand": base_demand,
            "capacity": base_capacity,
            "utilization": round(base_demand / (base_capacity + 1e-5), 4),
            "latency": 45.0,  # ms
            "error_rate": 0.001,  # 0.1%
            "revenue": base_demand * 0.50,  # $ per minute
        }
        return cls(nodes=nodes, edges=edges, base_values=base_values)

    def _topological_sort(self) -> list[str]:
        """Return nodes in topological dependency order."""
        in_degree = dict.fromkeys(self.nodes, 0)
        adj: dict[str, list[str]] = {node: [] for node in self.nodes}
        for edge in self.edges:
            adj[edge.source].append(edge.target)
            in_degree[edge.target] += 1

        queue = [n for n, deg in in_degree.items() if deg == 0]
        sorted_nodes: list[str] = []

        while queue:
            node = queue.pop(0)
            sorted_nodes.append(node)
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        for n in self.nodes:
            if n not in sorted_nodes:
                sorted_nodes.append(n)

        return sorted_nodes

    def propagate_linear(self, overrides: dict[str, float]) -> dict[str, float]:
        """Propagate multiplicative delta adjustments in topological order."""
        deltas: dict[str, float] = {}
        for node in self.nodes:
            if node in overrides:
                val = overrides[node]
                if node == "capacity" and val > 10.0:
                    base_cap = self.base_values.get("capacity", 4000.0)
                    deltas[node] = (val - base_cap) / base_cap
                else:
                    deltas[node] = val - 1.0 if val >= 0 else 0.0
            else:
                deltas[node] = 0.0

        topo_order = self._topological_sort()

        for node in topo_order:
            incoming_edges = [e for e in self.edges if e.target == node]
            if incoming_edges:
                cumulative_delta = deltas[node]
                for edge in incoming_edges:
                    cumulative_delta += deltas[edge.source] * edge.sensitivity
                deltas[node] = cumulative_delta

        result_values: dict[str, float] = {}
        for node, base_val in self.base_values.items():
            perturbed = base_val * (1.0 + deltas.get(node, 0.0))
            result_values[node] = round(max(0.0, perturbed), 4)

        return result_values

    def propagate_monte_carlo(
        self,
        overrides: dict[str, float],
        num_samples: int = 1000,
        sigma_scale: float = 0.05,
        seed: int = 42,
    ) -> dict[str, dict[str, float]]:
        """Sample perturbations from Gaussian distributions and propagate distributions."""
        rng = np.random.default_rng(seed)
        samples_by_node: dict[str, list[float]] = {n: [] for n in self.nodes}

        for _ in range(num_samples):
            sampled_overrides: dict[str, float] = {}
            for k, mean_val in overrides.items():
                scale = sigma_scale * abs(mean_val) if mean_val != 0 else sigma_scale
                noise = rng.normal(0.0, scale)
                sampled_overrides[k] = mean_val + noise

            run_vals = self.propagate_linear(sampled_overrides)
            for node, v in run_vals.items():
                samples_by_node[node].append(v)

        summary_results: dict[str, dict[str, float]] = {}
        for node, arr in samples_by_node.items():
            np_arr = np.array(arr)
            summary_results[node] = {
                "mean": round(float(np.mean(np_arr)), 2),
                "std": round(float(np.std(np_arr)), 2),
                "p10": round(float(np.percentile(np_arr, 10)), 2),
                "p50": round(float(np.percentile(np_arr, 50)), 2),
                "p90": round(float(np.percentile(np_arr, 90)), 2),
            }

        return summary_results
