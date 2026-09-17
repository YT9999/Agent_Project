"""Reusable evaluation harness for NovaRelay agent workflows.

The harness deliberately separates execution from scoring.  Production code can
provide a real orchestrator adapter, while unit tests can use a deterministic
fake without network access.
"""
from __future__ import annotations

import asyncio
import json
import math
import pathlib
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class HarnessResponse:
    text: str
    intent: str = ""
    agent: str = ""
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessCase:
    id: str
    input: str
    expected_intent: Optional[str] = None
    expected_agent: Optional[str] = None
    must_include: List[str] = field(default_factory=list)
    must_not_include: List[str] = field(default_factory=list)
    max_latency_ms: Optional[float] = None
    tags: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class QualityGate:
    metric: str
    operator: str
    value: float


@dataclass
class CaseResult:
    id: str
    passed: bool
    assertions: Dict[str, bool]
    response: HarnessResponse
    error: Optional[str] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class HarnessReport:
    suite: str
    run_id: str
    timestamp: str
    metrics: Dict[str, float]
    gates: Dict[str, bool]
    passed: bool
    regressions: List[str]
    cases: List[CaseResult]


Executor = Callable[[HarnessCase], Awaitable[HarnessResponse]]


class EvaluationHarness:
    """Run a versioned suite and enforce deterministic quality gates."""

    OPERATORS = {
        ">=": lambda actual, expected: actual >= expected,
        "<=": lambda actual, expected: actual <= expected,
        ">": lambda actual, expected: actual > expected,
        "<": lambda actual, expected: actual < expected,
        "==": lambda actual, expected: actual == expected,
    }

    def __init__(self, executor: Executor, concurrency: int = 4):
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._executor = executor
        self._concurrency = concurrency
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def run(
        self,
        suite_name: str,
        cases: Iterable[HarnessCase],
        gates: Iterable[QualityGate],
        baseline: Optional[Dict[str, Any]] = None,
    ) -> HarnessReport:
        case_list = list(cases)
        started = datetime.now(timezone.utc)
        # Create the semaphore inside the active loop (required by Python 3.9).
        self._semaphore = asyncio.Semaphore(self._concurrency)
        results = await asyncio.gather(*(self._run_case(case) for case in case_list))
        metrics = self._aggregate(results)
        gate_results = {
            f"{gate.metric} {gate.operator} {gate.value:g}": self._check_gate(gate, metrics)
            for gate in gates
        }
        regressions = self._compare_baseline(metrics, baseline or {})
        passed = all(result.passed for result in results) and all(gate_results.values()) and not regressions
        run_id = started.strftime("%Y%m%dT%H%M%SZ")
        return HarnessReport(
            suite=suite_name,
            run_id=run_id,
            timestamp=started.isoformat(),
            metrics=metrics,
            gates=gate_results,
            passed=passed,
            regressions=regressions,
            cases=results,
        )

    async def _run_case(self, case: HarnessCase) -> CaseResult:
        started = time.perf_counter()
        try:
            if self._semaphore is None:
                raise RuntimeError("harness must be started with run()")
            async with self._semaphore:
                response = await self._executor(case)
            measured_ms = (time.perf_counter() - started) * 1000
            if response.latency_ms <= 0:
                response = HarnessResponse(
                    text=response.text,
                    intent=response.intent,
                    agent=response.agent,
                    latency_ms=round(measured_ms, 2),
                    metadata=response.metadata,
                )
            assertions = self._assertions(case, response)
            return CaseResult(
                id=case.id,
                passed=all(assertions.values()),
                assertions=assertions,
                response=response,
                tags=case.tags,
            )
        except Exception as exc:  # one bad scenario must not abort a full suite
            return CaseResult(
                id=case.id,
                passed=False,
                assertions={"execution": False},
                response=HarnessResponse(text="", latency_ms=round((time.perf_counter() - started) * 1000, 2)),
                error=f"{type(exc).__name__}: {exc}",
                tags=case.tags,
            )

    @staticmethod
    def _assertions(case: HarnessCase, response: HarnessResponse) -> Dict[str, bool]:
        checks: Dict[str, bool] = {"non_empty": bool(response.text.strip())}
        if case.expected_intent is not None:
            checks["intent"] = response.intent == case.expected_intent
        if case.expected_agent is not None:
            checks["agent"] = response.agent == case.expected_agent
        if case.must_include:
            checks["must_include"] = all(token.casefold() in response.text.casefold() for token in case.must_include)
        if case.must_not_include:
            checks["must_not_include"] = all(token.casefold() not in response.text.casefold() for token in case.must_not_include)
        if case.max_latency_ms is not None:
            checks["latency"] = response.latency_ms <= case.max_latency_ms
        return checks

    @staticmethod
    def _aggregate(results: List[CaseResult]) -> Dict[str, float]:
        total = len(results)
        latencies = sorted(result.response.latency_ms for result in results)
        intent_cases = [r for r in results if "intent" in r.assertions]
        agent_cases = [r for r in results if "agent" in r.assertions]
        errors = [r for r in results if r.error]

        def ratio(numerator: int, denominator: int) -> float:
            return round(numerator / denominator, 4) if denominator else 0.0

        return {
            "case_pass_rate": ratio(sum(r.passed for r in results), total),
            "intent_accuracy": ratio(sum(r.assertions["intent"] for r in intent_cases), len(intent_cases)),
            "routing_accuracy": ratio(sum(r.assertions["agent"] for r in agent_cases), len(agent_cases)),
            "error_rate": ratio(len(errors), total),
            "p50_latency_ms": round(_percentile(latencies, 0.50), 2),
            "p95_latency_ms": round(_percentile(latencies, 0.95), 2),
            "total_cases": float(total),
        }

    def _check_gate(self, gate: QualityGate, metrics: Dict[str, float]) -> bool:
        if gate.metric not in metrics:
            return False
        if gate.operator not in self.OPERATORS:
            raise ValueError(f"unsupported gate operator: {gate.operator}")
        return bool(self.OPERATORS[gate.operator](metrics[gate.metric], gate.value))

    @staticmethod
    def _compare_baseline(metrics: Dict[str, float], baseline: Dict[str, Any]) -> List[str]:
        """Detect relative regressions using tolerances stored with the baseline."""
        previous = baseline.get("metrics", {})
        tolerances = baseline.get("regression_tolerance", {})
        lower_is_better = {"error_rate", "p50_latency_ms", "p95_latency_ms"}
        regressions: List[str] = []
        for name, tolerance in tolerances.items():
            if name not in metrics or name not in previous:
                continue
            old, new = float(previous[name]), float(metrics[name])
            allowed = abs(old) * float(tolerance)
            degraded = new > old + allowed if name in lower_is_better else new < old - allowed
            if degraded:
                regressions.append(f"{name}: baseline={old:g}, current={new:g}, tolerance={float(tolerance):.1%}")
        return regressions


def load_suite(path: pathlib.Path) -> tuple[str, List[HarnessCase], List[QualityGate]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = [HarnessCase(**item) for item in data.get("cases", [])]
    gates = [QualityGate(**item) for item in data.get("gates", [])]
    return str(data.get("name") or path.stem), cases, gates


def save_report(report: HarnessReport, output_dir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.suite}-{report.run_id}.json"
    markdown_path = output_dir / f"{report.suite}-{report.run_id}.md"
    json_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def render_markdown(report: HarnessReport) -> str:
    status = "PASS" if report.passed else "FAIL"
    lines = [
        f"# Evaluation report: {report.suite}",
        "",
        f"- Run: `{report.run_id}`",
        f"- Status: **{status}**",
        f"- Cases: {len(report.cases)}",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    lines.extend(f"| {name} | {value:g} |" for name, value in report.metrics.items())
    lines.extend(["", "## Quality gates", ""])
    lines.extend(f"- {'✅' if passed else '❌'} `{gate}`" for gate, passed in report.gates.items())
    if report.regressions:
        lines.extend(["", "## Regressions", ""])
        lines.extend(f"- {item}" for item in report.regressions)
    lines.extend(["", "## Cases", "", "| Case | Status | Intent | Agent | Latency |", "|---|---|---|---|---:|"])
    for result in report.cases:
        lines.append(
            f"| {result.id} | {'PASS' if result.passed else 'FAIL'} | "
            f"{result.response.intent or '-'} | {result.response.agent or '-'} | {result.response.latency_ms:.1f} ms |"
        )
    return "\n".join(lines) + "\n"


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    rank = max(0, math.ceil(percentile * len(values)) - 1)
    return values[rank]
