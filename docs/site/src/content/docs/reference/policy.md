---
title: Policy schema
description: TOML policy fields, defaults, constraints, and compatibility aliases.
sidebar:
  order: 2
---

TOML is the editable policy format. JSON policies remain readable. Unknown top-level fields are rejected. Use the public `type`/`name` schema in new policies.

## Root fields

| Field | Required | Default / constraint |
| --- | --- | --- |
| `version` | Yes | Integer `1` |
| `default_action` | No | `block`; `allow`, `filter`, `local`, `block` |
| `allowed_models` | No | When present: nonempty array of exact nonempty model names |
| `rules` | No | Array of input rules; built-ins remain loaded when omitted |
| `output` | No | Output policy table |
| `budgets` | No | Array of quota definitions |
| `budget_fallback` | For fallback budgets | Table with `route = "local"` or `"fallback"` |
| `permissions` | No | Table containing capability rules; default deny |

For explicit model control, define `allowed_models`; omission is not a wildcard policy declaration you should rely on. With preserve-model enabled and no explicit approvals, client selection is constrained to the configured main model. Main and alternate routes are checked against an explicit approval list when it exists.

## Input rule fields

| Field | Applies to | Meaning |
| --- | --- | --- |
| `type` | All | `builtin.<detector>`, `regex`, or `semantic` |
| `name` | Generic rules | Optional stable lowercase identifier; hash-derived when omitted |
| `action` | All | Input action; inherits root default when omitted |
| `pattern` | Regex | Required nonempty regex |
| `allowed_patterns` | Deterministic | Array of nonempty regex exceptions containing a rule's match |
| `validator` | Custom deterministic rules | Optional engine validator; see supported names below |
| `description` | Semantic | Required nonempty policy description |
| `threshold` | Semantic | Number 0–100, default 80 |

Semantic definitions cannot combine `pattern`, `validator`, or `allowed_patterns`. Built-in patterns and validators cannot be replaced. Custom deterministic validators are `pesel`, `bank`, `iban`, and `luhn`; prefer built-in detector types for standard formats. Rule labels must be unique and match `[a-z][a-z0-9_]*`.

## Output fields

| Field | Default / constraint |
| --- | --- |
| `inherit_input` | Boolean `true` |
| `default_action` | `block`; choices `block`, `block_device`, `warn` |
| `rules` | Array of output definitions or named input-rule overrides |
| Per-rule `action` | `block`, `block_device`, `warn`, `filter`, or `allow` |

Use an existing generic rule's type and name plus action to override it. New generic output rules need their complete definition. See [output enforcement](../../policies/output/) for inheritance and action precedence.

## Budget fields

| Field | Constraint |
| --- | --- |
| `name` | Required unique nonempty string |
| `type` | `fixed_quota` or `rolling_average` |
| `tokens` | `input`, `output`, `total` (default) |
| `limit` | Required finite positive number; tokens or tokens/second depending on type |
| `window_seconds` | Required finite positive number |
| `reset_at` | Required timezone-aware ISO timestamp for fixed quota; forbidden for rolling |
| `models` | Optional nonempty array of exact nonempty model names |
| `on_exhaustion` | `block` (default) or `fallback`; fallback requires destination table |

There is no per-client budget selector. See [token budgets](../../policies/budgets/) for accounting semantics.

## Permission fields

Each entry in `permissions.rules` contains all of:

| Field | Constraint |
| --- | --- |
| `name` | Unique nonempty string |
| `type` | `tool`, `mcp`, or `memory` |
| `resource` | Nonempty case-sensitive resource pattern |
| `operations` | Nonempty array; supported operations depend on type |
| `clients` | Nonempty identity array; `*` matches any authenticated identity |
| `action` | `allow` or `deny` |

Tool supports `call`; MCP supports `call`/`read`; memory supports `read`/`write`/`delete`. Deny wins. See [agent permissions](../../integrations/agent-permissions/).

## Legacy aliases

Unprefixed built-in detector types and internal `id`/`kind` fields remain readable for existing integrations. Do not provide both public and internal names on one entry: `name` plus `id`, or permission `type` plus `kind`, is invalid. Public generic rules use `type = "regex"` or `"semantic"` and optional `name`.

External dynamic policies may add only new restrictive input/output rules. They cannot edit budgets, permissions, model approvals, or defaults. See [administration](../../operations/administration/).
