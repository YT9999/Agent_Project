"""Command-line entry point for the NovaRelay evaluation harness."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import uuid

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.agent_orchestrator import AgentOrchestrator, Request
from core.skill_loader import SkillManager
from evaluation.harness import EvaluationHarness, HarnessResponse, load_suite, save_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run NovaRelay regression suites")
    parser.add_argument("--suite", type=pathlib.Path, default=ROOT / "evaluation" / "suites" / "smoke.json")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "evaluation" / "reports")
    parser.add_argument("--baseline", type=pathlib.Path, default=ROOT / "evaluation" / "baselines" / "smoke.json")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--promote", action="store_true", help="Promote this successful run to the regression baseline")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is required")

    model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022").strip()
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip() or None
    skill_manager = SkillManager(root_dir=str(ROOT / "skills"))
    skill_manager.load()
    orchestrator = AgentOrchestrator(
        api_key=api_key,
        base_url=base_url,
        model=model,
        skill_manager=skill_manager,
    )

    async def execute(case):
        result = await orchestrator.run(
            Request(
                message=case.input,
                user_id="harness",
                conv_id=f"eval-{case.id}-{uuid.uuid4().hex[:6]}",
            )
        )
        return HarnessResponse(
            text=result.response,
            intent=result.intent.value if result.intent else "",
            agent=result.agent_type.value,
            latency_ms=result.latency_ms,
            metadata={
                "request_id": result.request_id,
                "tools_used": result.tools_used,
                "routing_reason": result.routing_reason,
            },
        )

    suite_name, cases, gates = load_suite(args.suite)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else None
    report = await EvaluationHarness(execute, args.concurrency).run(suite_name, cases, gates, baseline)
    json_path, markdown_path = save_report(report, args.output)
    print(f"{'PASS' if report.passed else 'FAIL'} {suite_name}")
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")

    if args.promote:
        if not report.passed:
            print("Refusing to promote a failed run.")
            return 1
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(
                {
                    "suite": report.suite,
                    "promoted_from": report.run_id,
                    "metrics": report.metrics,
                    "regression_tolerance": {
                        "case_pass_rate": 0.05,
                        "intent_accuracy": 0.05,
                        "routing_accuracy": 0.05,
                        "error_rate": 0.05,
                        "p95_latency_ms": 0.20,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Baseline promoted: {args.baseline}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
