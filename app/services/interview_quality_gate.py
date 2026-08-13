from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_GATE_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "interview_quality_v1_gate.json"
)

GateOperator = Literal["gte", "lte", "eq", "record"]
GateSeverity = Literal["blocking", "warning"]
BaselineMode = Literal["none", "max_lower", "min_upper"]
GateResultStatus = Literal[
    "PASS",
    "FAIL",
    "WARNING",
    "RECORDED",
    "INSUFFICIENT_SAMPLE",
    "INSUFFICIENT_BASELINE",
]


class MetricRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: GateOperator
    threshold: float | None
    unit: str = Field(min_length=1)
    severity: GateSeverity
    min_sample_size: int = Field(ge=1)
    formula: str | None = None
    baseline_mode: BaselineMode = "none"
    baseline_multiplier: float = Field(default=1.0, gt=0)
    requires_comparable_baseline: bool = False

    @model_validator(mode="after")
    def validate_rule(self):
        if self.operator == "record":
            if self.threshold is not None:
                raise ValueError("record metrics must not have a threshold")
            if self.baseline_mode != "none" or self.requires_comparable_baseline:
                raise ValueError("record metrics cannot use a baseline")
        elif self.threshold is None:
            raise ValueError("gated metrics require a numeric threshold")
        if self.baseline_mode == "max_lower" and self.operator != "gte":
            raise ValueError("max_lower baseline mode requires gte")
        if self.baseline_mode == "min_upper" and self.operator != "lte":
            raise ValueError("min_upper baseline mode requires lte")
        return self


class GateChangePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_evidence: list[str] = Field(min_length=1)
    lower_threshold_to_pass_current_run: bool


class GateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["interview-quality-gate-config-v1"]
    config_id: str = Field(min_length=1)
    effective_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    change_policy: GateChangePolicy
    formulas: dict[str, str]
    algorithm_parameters: dict[str, float]
    metric_groups: dict[str, dict[str, MetricRule]]
    cohort_dimensions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_config(self):
        if self.change_policy.lower_threshold_to_pass_current_run:
            raise ValueError("threshold lowering for the current run must be disabled")
        if len(self.cohort_dimensions) != len(set(self.cohort_dimensions)):
            raise ValueError("cohort_dimensions must be unique")
        if not self.metric_groups or any(not rules for rules in self.metric_groups.values()):
            raise ValueError("every metric group must contain at least one rule")
        for group, rules in self.metric_groups.items():
            for name, rule in rules.items():
                if rule.formula is not None and rule.formula not in self.formulas:
                    raise ValueError(f"unknown formula for {group}.{name}: {rule.formula}")
        return self

    def resolve_rule(self, metric_key: str) -> MetricRule:
        try:
            group, metric = metric_key.split(".", 1)
            return self.metric_groups[group][metric]
        except (KeyError, ValueError) as exc:
            raise KeyError(f"unknown GateConfig metric: {metric_key}") from exc


class MetricEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_key: str
    status: GateResultStatus
    blocking: bool
    actual: float
    operator: GateOperator
    configured_threshold: float | None
    effective_threshold: float | None
    unit: str
    sample_size: int
    minimum_sample_size: int
    baseline: float | None = None
    deviation: float | None = None
    reason: str


def load_gate_config(path: Path | str = DEFAULT_GATE_CONFIG_PATH) -> GateConfig:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return GateConfig.model_validate(payload)


def gate_config_sha256(path: Path | str = DEFAULT_GATE_CONFIG_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evaluate_metric(
    config: GateConfig,
    metric_key: str,
    *,
    actual: float,
    sample_size: int,
    baseline: float | None = None,
) -> MetricEvaluation:
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        raise TypeError("actual must be a number")
    if isinstance(sample_size, bool) or not isinstance(sample_size, int) or sample_size < 0:
        raise ValueError("sample_size must be a non-negative integer")
    if baseline is not None and (
        isinstance(baseline, bool) or not isinstance(baseline, (int, float))
    ):
        raise TypeError("baseline must be a number or None")

    rule = config.resolve_rule(metric_key)
    common = {
        "metric_key": metric_key,
        "blocking": rule.severity == "blocking",
        "actual": float(actual),
        "operator": rule.operator,
        "configured_threshold": rule.threshold,
        "effective_threshold": rule.threshold,
        "unit": rule.unit,
        "sample_size": sample_size,
        "minimum_sample_size": rule.min_sample_size,
        "baseline": float(baseline) if baseline is not None else None,
    }
    if sample_size < rule.min_sample_size:
        return MetricEvaluation(
            **common,
            status="INSUFFICIENT_SAMPLE",
            reason=f"sample_size={sample_size} is below {rule.min_sample_size}",
        )
    if rule.requires_comparable_baseline and baseline is None:
        return MetricEvaluation(
            **common,
            status="INSUFFICIENT_BASELINE",
            reason="a comparable frozen baseline is required",
        )
    if rule.operator == "record":
        return MetricEvaluation(
            **common,
            status="RECORDED",
            reason="usage is recorded without an authorization ceiling",
        )

    threshold = _effective_threshold(rule, baseline)
    assert threshold is not None
    passed = _compare(rule.operator, float(actual), threshold)
    if passed:
        status: GateResultStatus = "PASS"
    elif rule.severity == "blocking":
        status = "FAIL"
    else:
        status = "WARNING"
    deviation = float(actual) - threshold
    return MetricEvaluation(
        **(common | {"effective_threshold": threshold}),
        status=status,
        deviation=deviation,
        reason=(
            f"actual={float(actual):g} {rule.operator} threshold={threshold:g}"
            if passed
            else f"actual={float(actual):g} violates {rule.operator} threshold={threshold:g}"
        ),
    )


def _effective_threshold(rule: MetricRule, baseline: float | None) -> float | None:
    threshold = rule.threshold
    if threshold is None or baseline is None or rule.baseline_mode == "none":
        return threshold
    candidate = float(baseline) * rule.baseline_multiplier
    if rule.baseline_mode == "max_lower":
        return max(threshold, candidate)
    return min(threshold, candidate)


def _compare(operator: GateOperator, actual: float, threshold: float) -> bool:
    if operator == "gte":
        return actual >= threshold
    if operator == "lte":
        return actual <= threshold
    if operator == "eq":
        return actual == threshold
    raise ValueError("record metrics do not support threshold comparison")


def render_metric_markdown(result: MetricEvaluation) -> str:
    threshold = (
        "record-only"
        if result.effective_threshold is None
        else f"{result.operator} {result.effective_threshold:g} {result.unit}"
    )
    return "\n".join(
        [
            "| Metric | Status | Actual | Gate | Sample | Reason |",
            "| --- | --- | ---: | --- | ---: | --- |",
            (
                f"| `{result.metric_key}` | **{result.status}** | "
                f"{result.actual:g} {result.unit} | {threshold} | "
                f"{result.sample_size}/{result.minimum_sample_size} | {result.reason} |"
            ),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate one Interview Quality V1 gate")
    parser.add_argument("--config", type=Path, default=DEFAULT_GATE_CONFIG_PATH)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--actual", required=True, type=float)
    parser.add_argument("--sample-size", required=True, type=int)
    parser.add_argument("--baseline", type=float)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)

    config = load_gate_config(args.config)
    result = evaluate_metric(
        config,
        args.metric,
        actual=args.actual,
        sample_size=args.sample_size,
        baseline=args.baseline,
    )
    if args.format == "markdown":
        print(render_metric_markdown(result))
    else:
        print(result.model_dump_json(indent=2))
    if result.status == "FAIL":
        return 1
    if result.status in {"INSUFFICIENT_SAMPLE", "INSUFFICIENT_BASELINE"}:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
