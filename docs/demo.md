# Demo and submission checklist

## Rehearsal

1. Run `uv sync --all-packages`, copy `.env.example` to `.env`, and set the upstream and Jev keys. For Laya, install `--extra laya` and select `STOPSLOP_CLASSIFIER=laya`.
2. Run `uv run --no-sync pytest -q`; save the test summary with your submission evidence. Tests use mocked providers and classifiers, with no API credentials or calls.
3. Verify the configured chat model appears in `policy.toml`'s `allowed_models`. Configure and approve a local model if demonstrating local privacy or budget fallback. Keep the same absolute state path in the demo, gateway and dashboard.
4. Run `just top` in another terminal, then `just demo`. Show an allowed turn, anonymized forwarding with restoration, and a blocked NDA disclosure. The scenario verifies each expected action and exits with an error on mismatch.
5. Show the violation and chat tabs, then export audit metadata with `stopslop-policy export audit --state-file PATH`. Use invented data and keep keys out of recordings.
6. If showing SDK integration, run `just proxy` and point a text Chat Completions client at it. Demonstrate rejection before forwarding and output enforcement before delivery.
7. Rehearse policy reload and a small model-scoped budget with a configured fallback. Demonstrate that an unscoped hard cap still blocks. Select `STOPSLOP_FALLBACK_CLASSIFIER` explicitly if paid assessment must fall back too.

`just demo-local --model INSTALLED_MODEL` uses loopback Ollama and Laya. When switching
providers, update all four upstream settings together. Changing the provider flag does not
clear explicit environment endpoint, model or key values.

## Evidence by criterion

| Criterion | Weight in criteria | Evidence to present |
| --- | --- | --- |
| Robustness | 30% | Positive/negative enforcement, fail-closed classifier/output handling, bounded payloads, quotas and authenticated permissions |
| Architecture | 20% | Four package boundaries; one shared engine for SDK and gateway; runtime/policy separation; repository boundary |
| Reporting | 20% | Timestamped violations, decisions, actual model and fallback usage, dashboard and JSON audit export |
| Testing | 15% | Automated mock suite plus recorded live rehearsal; explicit distinction between these evidence sources |
| Scalability | 15% | Shared transactional admission across local processes and HTTP gateway for remote clients; identify local SQLite limits |

Rules assign testing 20% and scalability 10%; clarify the discrepancy with organizers.
Do not claim measured real-classifier accuracy or distributed scaling from mocked tests.
Record live latency and failures separately if you measure them during rehearsal.

## Submission

Prepare project/team details and at most ten PDF slides. Suggested order: problem, threat
model, architecture, configuration/policy example, privacy demo, semantic/output checks,
quotas/permissions, reporting, validation evidence, limits and next steps. The rules state
Oct 3 23:00 through Oct 4 23:00 for the competition window; confirm organizer timing.
Never include `.env`, credentials or private runtime databases in submission assets.
