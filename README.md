# NovaRelay

NovaRelay is an observable multi-agent runtime for customer-support and operations workflows. It turns a user request into a traceable pipeline: intent recognition, retrieval, specialist routing, response composition, memory persistence, monitoring, and regression evaluation.

> Portfolio note: quality numbers are intentionally not hard-coded in this README. Run the evaluation harness on your own model and environment to generate reproducible reports.

## Why this project exists

Production agents fail in more ways than “the answer looks wrong”: a router can select the wrong specialist, a tool can leak an unsafe value, latency can regress, or a prompt change can silently lower intent accuracy. NovaRelay treats those behaviors as versioned test cases and quality gates.

## Highlights

- Four specialist nodes: General, Technical, Billing, and Escalation
- Hybrid intent recognition with confidence, urgency, and entity extraction
- Intent-aware RAG with ChromaDB and a shared tool contract
- Redis working memory plus vector-backed episodic memory
- Runtime-loaded Skills for changing business policies without code edits
- Prometheus metrics, tool traces, fallback paths, and route degradation
- Versioned evaluation suites with assertions, aggregate gates, baseline comparison, JSON/Markdown artifacts, and explicit baseline promotion

## Architecture

```text
Request
  -> memory context
  -> intent + urgency + entities
  -> knowledge retrieval (when needed)
  -> specialist router
       -> General / Technical / Billing / Escalation
  -> response composer
  -> memory + metrics + tool trace
  -> evaluation harness / regression gates
```

Detailed diagrams live in [wiki/架构图.md](wiki/架构图.md).

## Quick start

Prerequisites: Docker, Docker Compose, and an Anthropic-compatible API key.

```bash
cp .env.example .env
# edit ANTHROPIC_API_KEY in .env
docker compose up -d --build
curl http://localhost:8000/health
```

Useful endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /chat` | Run the complete agent pipeline |
| `GET /trace/tools` | Inspect recent tool traces |
| `GET /monitor` | Read runtime health and routing metrics |
| `POST /eval/run` | Run LLM-as-Judge evaluation |
| `POST /eval/harness` | Run a versioned regression suite and quality gates |
| `GET /docs` | OpenAPI documentation |

## Evaluation harness

The smoke suite lives at [evaluation/suites/smoke.json](evaluation/suites/smoke.json). Each case can assert intent, selected agent, required/forbidden response fragments, and maximum latency. Suite-level gates cover pass rate, routing accuracy, intent accuracy, error rate, and p95 latency.

```bash
# Run without changing the approved baseline
python -m evaluation.run_harness

# Promote only a successful run
python -m evaluation.run_harness --promote
```

Reports are written to `evaluation/reports/` as JSON and Markdown. A failed case is isolated instead of aborting the suite, which makes provider outages and individual scenario failures visible in the same report.

## Development

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest -q
```

The unit suite does not call a real model. Live harness runs require `ANTHROPIC_API_KEY` and are intentionally separate from pull-request tests.

## Repository map

```text
api/main.py                    FastAPI application and public contracts
agents/agent_orchestrator.py   routing, specialist agents, tool loop, composition
core/intent_recognizer.py      intent, urgency, confidence, and entities
core/skill_loader.py           runtime business-skill loading
mcp/                           knowledge and tool execution layer
memory/                        working and episodic memory
monitor/                       health, metrics, and degradation signals
evaluation/evaluator.py        LLM-as-Judge evaluator
evaluation/harness.py          deterministic regression harness
evaluation/suites/             versioned scenario datasets
tests/                          offline unit tests
wiki/                           architecture and design documentation
```

## Security

- Never commit `.env`; use `.env.example` as the schema.
- Change the example Redis password before deployment.
- Tool execution is restricted by per-agent allowlists and validated schemas.
- Publish generated evaluation reports only when they are safe to share and reproducible.

## Project status

NovaRelay is an engineering portfolio project, not a hosted customer-support service. See [wiki/项目讲解指南.md](wiki/项目讲解指南.md) for design decisions, trade-offs, and a demo checklist.
