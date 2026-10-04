---
title: Rules and input actions
description: Author TOML policies with built-in detectors, custom regexes, and semantic rules.
sidebar:
  order: 1
---

Policies define enforcement decisions. Runtime settings select providers, credentials, classifiers, and storage. Keep these two layers separate: `policy.toml` does not accept provider or classifier configuration.

## Start with a minimal policy

```toml
version = 1
default_action = "filter"
allowed_models = ["your-approved-model"]

[[rules]]
type = "builtin.secret"
action = "block"

[[rules]]
type = "regex"
name = "internal_project"
pattern = '\bProject Kestrel\b'
action = "block"
```

Save the file and select it with `STOPSLOP_POLICY_FILE` or `--policy-file`. Otherwise `Settings.load()` discovers `policy.toml` in the current directory when no explicit policy or detector catalog has been selected.

:::caution[Omitted rules remain active]
Built-in detectors are loaded even when their entries are omitted. `default_action` supplies an action for detector matches without an explicit override; it does not make clean text unsafe. Set a built-in rule to `action = "allow"` to disable enforcement for that category.
:::

## Input actions

| Action | Result |
| --- | --- |
| `allow` | Matching content may proceed without that rule imposing a restriction |
| `filter` | Replace matching spans with unique anonymous tokens before forwarding |
| `local` | Route the request to the configured loopback model; filter matches still apply |
| `block` | Reject the request before a chat-provider call |

When several rules match, **block > local > filter > allow**. An allow rule does not override another rule's block. To exempt a known value, use a contained-match exception for the particular deterministic rule.

## Built-in rules

```toml
[[rules]]
type = "builtin.email"
action = "filter"
allowed_patterns = ['\bsupport@example\.com\b']
```

The exception applies only when it fully contains a match of the same rule. It is not a global allowlist. See [detector catalog](../../reference/detectors/) for all built-ins and their recognition limits.

## Custom regex rules

```toml
[[rules]]
type = "regex"
name = "customer_reference"
pattern = '\bCUST-[0-9]{6}\b'
action = "filter"
allowed_patterns = ['\bCUST-000000\b']
```

Patterns use the Python `regex` engine with case-insensitive matching. Rules have a 50 ms per-rule scan timeout; timeout fails closed. TOML literal strings (`'...'`) avoid escaping backslashes twice.

Use unique lowercase identifiers matching `[a-z][a-z0-9_]*`. A `name` is optional for generic rules; unnamed definitions receive a stable hash-derived label. Explicit names make audit records and output overrides easier to maintain. Multiple `type = "regex"` entries are supported. Built-in patterns cannot be overwritten: add a separately named custom rule.

## Semantic rules

```toml
[[rules]]
type = "semantic"
name = "confidential_information"
description = "Do not disclose NDA-protected technical designs or private launch plans to unauthorized recipients. Generic agendas, email signatures, public information, and general NDA explanations are allowed."
threshold = 70
action = "block"
```

Thresholds are percentages from 0 to 100; the default is 80. Describe both prohibited content and allowed context. Semantic rules cannot also contain a regex pattern, validator, or `allowed_patterns`. Select an assessment backend separately; see [classifiers](../classifiers/).

`--deterministic` explicitly skips semantic checks. It is useful for deterministic-only evaluation, but changes which controls are active. Classifier failures otherwise block forwarding.

## Redaction and restoration

Input filtering replaces matched spans with unique request-local tokens and retains the replacement dictionary in memory. The provider receives tokenized text. After output inspection, exact returned tokens are restored locally in one pass. A token paraphrased or altered by the provider cannot be restored reliably. The replacement dictionary is not recorded in chat logs.

Output filtering handles newly generated sensitive text separately; it does not promise reversible restoration for newly generated content.

## Reload behavior

The policy loader checks for updates before each request. A validated change applies to the next request; an in-flight request keeps its input policy, output policy, and budget snapshot. Invalid or missing configured policy files fail closed rather than continuing with a stale policy. Provider and classifier settings remain fixed for the running process.

Write policy changes atomically where possible so another process does not observe a partially written file. Keep policies under version control and evaluate changes with both allowed and prohibited examples. See [policy reference](../../reference/policy/) and [administration](../../operations/administration/) for dynamic additions.

## Shipped NDA demo rule

The shipped `confidential_semantic` rule uses a threshold of 70, reduced from 75 for local Laya assessment. The allowed turns explicitly describe a public meeting and its invitation signature. The prohibited turn states the intent to breach an NDA and leak the protected sensor design and launch date to an unauthorized supplier. This separates public planning from the actual disclosure request while preserving the rule’s scope for non-public company information.

The `nda` scenario demonstrates a semantic block: its final request does not match a deterministic blocking pattern. The `local` scenario retains an explicit confidentiality marker for deterministic comparison. Thresholds are decision cutoffs on classifier scores; use benign and prohibited examples to tune your deployment rather than treating the shipped value as universal calibration.
