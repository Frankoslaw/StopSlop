---
title: Output enforcement
description: Inspect generated text, redact disclosures, and suspend authenticated clients.
sidebar:
  order: 2
---

StopSlop inspects generated message text before restoring input redaction tokens and delivering the response. Output controls use the policy snapshot captured at request admission.

## Configure output policy

```toml
[output]
inherit_input = true
default_action = "block"

[[output.rules]]
type = "builtin.profanity"
action = "warn"

[[output.rules]]
type = "builtin.private_key"
action = "block_device"
```

`inherit_input` defaults to `true`. Inherited input `allow` becomes output `allow`, and input `filter` becomes output `filter`. Other inherited input actions use the output default, which defaults to `block`. Explicit output overrides take priority for their rule.

The output default accepts `block`, `block_device`, or `warn`. Individual rules also accept `filter` and `allow`.

## Output actions

| Action | Delivery | Side effect |
| --- | --- | --- |
| `allow` | Deliver matching text | No restriction from that rule |
| `warn` | Deliver matching text | Record the violation and response metadata |
| `filter` | Deliver redacted text | Record the violation |
| `block` | Withhold the response | Return `output_blocked` |
| `block_device` | Withhold the response | Suspend the authenticated client identity |

Output precedence is **block_device > block > filter > warn > allow**. With no authenticated identity, `block_device` still withholds the response but has no client identity to suspend. A suspension persists in shared state until explicitly reset.

## Override a named input rule

```toml
[[rules]]
type = "semantic"
name = "confidential_information"
description = "Do not disclose NDA-protected information. Public information is allowed."
threshold = 75
action = "block"

[output]
inherit_input = true
default_action = "block"

[[output.rules]]
type = "semantic"
name = "confidential_information"
action = "block_device"
```

A named output override can reuse the input definition without repeating the description or pattern. Output-only generic rules need their own complete definition. Disabling `inherit_input` removes inherited checks; explicit output rules still apply.

## Accounting and availability

Generated work is charged even when its response is withheld. A classifier failure during output inspection also withholds delivery. An independent fallback classifier may handle eligible assessment budget exhaustion, but does not make classifier failures permissive.

Unsupported response shapes are rejected before delivery. See [supported usage](../../start/compatibility/). For suspension recovery, use `stopslop-policy reset-client CLIENT_ID --state-file PATH`; see [administration](../../operations/administration/).
