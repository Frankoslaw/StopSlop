---
title: Administration and adaptation
description: Inspect reservations, reset suspensions, and generate bounded restrictive policy additions.
sidebar:
  order: 4
---

The `stopslop-policy` command ships with the core package. Run it in an installed environment or prefix examples with `uv run --no-sync --package stopslop` in this workspace. Always select the same state file used by enforcement.

## Inspect and release orphan reservations

```sh
stopslop-policy reservations --state-file /absolute/path/stopslop.sqlite3
stopslop-policy release-orphan RESERVATION_ID --state-file /absolute/path/stopslop.sqlite3
```

Reservations survive crashes and do not automatically expire. Confirm that the owning process and remote provider work have stopped before releasing one. The command refuses a live or unknown owner process. Releasing capacity while work still runs can defeat quota admission.

## Reset a suspended identity

```sh
stopslop-policy reset-client demo-agent --state-file /absolute/path/stopslop.sqlite3
```

This clears the identity's suspension. It does not delete usage history, change permissions, or rotate the client token. Investigate the output violation that caused suspension before allowing new work.

## Record incident metadata

```sh
stopslop-policy record --kind code_execution --rule shell_execution --state-file /absolute/path/stopslop.sqlite3
```

Accepted kinds are `code_execution`, `data_leak`, `prompt_injection`, `unsafe_deserialization`, `supply_chain`, `input_violation`, and `output_violation`. Repeat `--rule` to attach several rule identifiers. Incident records support metadata-driven generation; they are not raw chat transcripts.

## Generate restrictive additions

```sh
stopslop-policy generate --policy-file policy.toml --state-file /absolute/path/stopslop.sqlite3
```

With `STOPSLOP_AUTOGEN=true` (enabled in the example dotenv), recorded input and output violations schedule local Qwen generation in a daemon worker. Use `--autogen` / `--no-autogen` or set the environment value to `false` to control it. This responds to detected violations, including unsafe output that passed input checks; it cannot discover an entirely undetected bypass. The worker sends incident metadata and policy definitions, never raw chats. It validates restrictive additions, saves them in SQLite, and atomically exports `policy.dyn.toml` (`STOPSLOP_AUTOGEN_OUTPUT_FILE`). Subsequent requests reload validated additions. Generation failures preserve enforcement and do not delay responses. Local generation requests a constrained JSON schema and disables thinking through [Ollama’s compatible API](https://docs.ollama.com/api/openai-compatibility); all responses still pass independent policy validation.

Scheduling allows one job per state file per process, coalescing bursts with a 60-second cooldown after completion. Incidents during a job or cooldown remain stored for the next eligible incident or explicit generation command; shutdown does not wait for the daemon. Multiple gateway processes have independent scheduling. Set `STOPSLOP_AUTOGEN=false` when only explicit generation is desired. Do not also load the generated export through `STOPSLOP_DYNAMIC_POLICY_FILE`, because those rules already load from SQLite.

Generation uses `STOPSLOP_LOCAL_*` when a local model is configured; otherwise the explicit command uses the main provider. The shipped local model is `qwen3:0.6b`. Model approvals and quota reservation still apply. Start Ollama with `just ollama-serve`; an existing Ollama service already serves installed models.

For an optional inspection export:

```sh
stopslop-policy generate --policy-file policy.toml --state-file /absolute/path/stopslop.sqlite3 --output policy.dyn.toml
```

The generation command activates validated additions in shared state; the export is not a draft awaiting a second activation step. Review resulting rules and their effect on allowed fixtures.

## External additive overlays

`STOPSLOP_DYNAMIC_POLICY_FILE` loads an externally managed additive overlay alongside a base policy. It requires `policy_file`. Only new restrictive input rules and output rules are allowed; budgets, permissions, approvals, and default actions cannot be changed by an overlay. Invalid additions fail closed.

Generated SQLite policy does not require `--dynamic-policy-file`. Avoid loading an exported copy of already active generated rules as a second overlay: duplicate rule names are invalid.

## Development cleanup

`just clean` resets local workspace state after all processes stop. It removes quota history and generated additions along with databases and legacy artifacts. It is intentionally broader than resetting one identity or releasing one reservation. See [storage](../storage/) for its scope.
