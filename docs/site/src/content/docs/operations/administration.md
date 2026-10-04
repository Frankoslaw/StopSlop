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

Generation is explicit and uses the configured chat model. It sends incident metadata and policy definitions, never raw chat records. The result must pass bounded restrictive validation: new input rules block; new output rules block or suspend; additions cannot weaken existing controls. Validated rules are saved in SQLite and picked up by the next request.

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
