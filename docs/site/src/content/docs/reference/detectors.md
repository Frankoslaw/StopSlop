---
title: Built-in detector catalog
description: Built-in deterministic rule names, format coverage, and validation boundaries.
sidebar:
  order: 3
---

Use these names with the `builtin.` prefix. The shipped `policy.toml` chooses actions; the detectors themselves define recognized formats. Omitted built-in entries still run with the policy's default action.

| Type | Recognizes | Validation / limits |
| --- | --- | --- |
| `builtin.pesel` | 11-digit Polish PESEL with optional spaces/hyphens | PESEL validation |
| `builtin.bank_account` | 26-digit Polish account number, optional `PL` and spaces | Bank-account validation |
| `builtin.phone` | 9-digit Polish-style number, optional `+48` and separators | Format match; can overlap numeric text |
| `builtin.polish_name` | A small explicit Polish first/surname catalog | Does not detect arbitrary Polish names |
| `builtin.email` | Conventional local-part and dotted-domain addresses | No mailbox existence check |
| `builtin.international_phone` | Non-Polish `+` numbers with 7–15 digits | Format match |
| `builtin.domestic_phone` | North American-style 3-3-4 format | Requires separators or parenthesized area code |
| `builtin.iban` | Selected European country layouts | IBAN validation; not all countries |
| `builtin.credit_card` | 13–19 digits with optional spaces/hyphens | Luhn validation; not card ownership |
| `builtin.personal_name` | Names after `name:`, `full name:`, or Polish label | Label-dependent, limited word count |
| `builtin.address` | Text after `address:` or `adres:` | Label-dependent, bounded line span |
| `builtin.date_of_birth` | Numeric date after DOB labels | Format match; not general unlabeled dates |
| `builtin.national_id` | Identifier after SSN/passport/national-ID labels | Label-dependent format match |
| `builtin.secret` | Values after password/API-key/access-token labels | Does not cover every unlabeled credential format |
| `builtin.private_key` | PEM-style private-key blocks | RSA, EC, OpenSSH, and generic private-key headers |
| `builtin.profanity` | A small explicit English/Polish word catalog | Not a general harassment classifier |

The IBAN pattern includes DE, GB, IE, PL, FR, IT, ES, NL, BE, CH, AT, and PT. Recognition depends on the supplied layouts and validator, not a full international financial-data catalog.

## Shipped policy choices

The example policy filters personal/contact/financial identifiers, blocks secrets and private keys, and routes profanity locally. It also supplies custom attack-signature regexes and semantic confidentiality, privacy, abuse, and execution rules. These custom rules are policy examples, not additional built-in detector types.

## Add domain-specific coverage

Use named regexes for structured internal identifiers, and semantic rules for paraphrased confidentiality or abuse. Validate intended benign examples as well as violations. A checksum-valid financial number is not necessarily sensitive in every context; use contained-match exceptions only where appropriate.

```toml
[[rules]]
type = "builtin.email"
action = "filter"
allowed_patterns = ['\bsupport@example\.com\b']
```

This exempts the known support address for the email detector, without exempting a secret or another rule's match in the same message. See [rules and input actions](../../policies/rules/).

## Deterministic scan performance

Regex patterns remain compiled for the loaded policy. Exception patterns are scanned once per rule per text, reusing their spans for every hit. Explicit output rules scan only their selected deterministic detectors; inherited input checks remain separate. Rules without identifier validators skip identifier normalization. Timeout handling and redaction behavior remain unchanged.
