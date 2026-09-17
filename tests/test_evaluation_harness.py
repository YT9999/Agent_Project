import asyncio
import json

from evaluation.harness import (
    EvaluationHarness,
    HarnessCase,
    HarnessResponse,
    QualityGate,
    render_markdown,
)


async def fake_executor(case):
    routes = {
        "tech": ("technical_login", "technical", "请先检查 Token 是否过期。"),
        "bill": ("payment_issue", "billing", "请提供订单号以便核对扣款。"),
    }
    intent, agent, text = routes[case.id]
    return HarnessResponse(text=text, intent=intent, agent=agent, latency_ms=42)


def test_harness_aggregates_metrics_and_enforces_gates():
    cases = [
        HarnessCase("tech", "401", expected_intent="technical_login", expected_agent="technical", must_include=["Token"]),
        HarnessCase("bill", "重复扣款", expected_intent="payment_issue", expected_agent="billing", must_include=["订单号"]),
    ]
    gates = [
        QualityGate("case_pass_rate", ">=", 1.0),
        QualityGate("p95_latency_ms", "<=", 100),
    ]

    report = asyncio.run(EvaluationHarness(fake_executor, concurrency=2).run("unit", cases, gates))

    assert report.passed is True
    assert report.metrics["intent_accuracy"] == 1.0
    assert report.metrics["routing_accuracy"] == 1.0
    assert all(report.gates.values())
    assert "Evaluation report: unit" in render_markdown(report)


def test_harness_reports_regression_without_overwriting_baseline():
    case = HarnessCase("tech", "401", expected_intent="technical_login", expected_agent="technical")
    baseline = {
        "metrics": {"p95_latency_ms": 20, "case_pass_rate": 1.0},
        "regression_tolerance": {"p95_latency_ms": 0.1, "case_pass_rate": 0.05},
    }

    report = asyncio.run(EvaluationHarness(fake_executor).run("unit", [case], [], baseline=baseline))

    assert report.passed is False
    assert any("p95_latency_ms" in item for item in report.regressions)


def test_execution_failure_is_isolated_to_one_case():
    async def broken_executor(case):
        raise RuntimeError("provider unavailable")

    report = asyncio.run(
        EvaluationHarness(broken_executor).run(
            "failure",
            [HarnessCase("broken", "hello")],
            [QualityGate("error_rate", "<=", 0.0)],
        )
    )

    assert report.passed is False
    assert report.metrics["error_rate"] == 1.0
    assert report.cases[0].error == "RuntimeError: provider unavailable"
