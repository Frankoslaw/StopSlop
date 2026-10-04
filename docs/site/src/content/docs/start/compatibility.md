---
title: Supported usage
description: Supported integrations, request fields, and protocol boundaries.
---

## Integration matrix

| Usage | Support | Notes |
| --- | --- | --- |
| Synchronous Python Chat Completions via HTTP | Supported | Use the gateway `/v1` base URL |
| Asynchronous Python Chat Completions via HTTP | Supported | Use `AsyncOpenAI` against the gateway |
| Other languages or direct HTTP | Supported | Send the documented JSON request and bearer token |
| Synchronous Python in-process enforcement | Supported | `httpx.Client(transport=PolicyRouter(settings))` |
| Async in-process transport | Unsupported | Use the HTTP gateway |
| Multi-turn text conversations | Supported | Resend text history; all supplied messages are inspected |
| Separate tool/MCP/memory authorization | Supported | `AgentGuard` or `/v1/authorize` |
| Chat tool calls / function calling | Unsupported | Keep capability invocation outside Chat Completions |
| Streaming / SSE | Unsupported | `stream` must be absent or `false` |
| Images, audio, video, content-part arrays | Unsupported | Message content must be a string |
| Responses API, embeddings, files, model listing | Unsupported | No compatibility promise for these endpoints |
| Native Ollama `/api/chat` | Unsupported | Use Ollama's `/v1` compatibility endpoint |
| Distributed database coordination | Unsupported | SQLite requires local-disk transactional access |

An OpenAI-compatible provider is not a guarantee that every SDK feature passes through. The request allowlist is deliberately small.

## Accepted request shape

| Field | Constraint |
| --- | --- |
| `model` | Nonempty string when supplied; defaults to configured model |
| `messages` | Nonempty array, at most 1,024 entries |
| Message fields | Exactly `role` and `content`; no `name` or tool metadata |
| `role` | `system`, `developer`, `user`, or `assistant` |
| `content` | String; combined UTF-8 content at most 1 MiB |
| `max_tokens` | Positive integer; reservation defaults to 1,024 when omitted |
| `temperature` | Finite number from 0 to 2 |
| `top_p` | Finite number from 0 to 1 |
| `stream` | Absent or literal `false` |
| `reasoning_budget` | Integer from -1 to 32,768; provider must support it |

Any other top-level request field is rejected. In particular, `max_completion_tokens`, `tools`, `tool_choice`, `response_format`, `seed`, `n`, and `metadata` are outside the contract. Frameworks may add these automatically; inspect the actual payload when integrating.

The HTTP body limit (default 2 MiB) is separate from the 1 MiB combined text limit. Increasing the former does not increase the latter.

## Supported response shape

The response must be a JSON object with a `choices` array containing message objects. Generated message fields are inspected as text; nonempty `tool_calls`, `function_call`, and `audio` are rejected. Non-string generated fields, except role and null fields, are also rejected. A provider's extra reasoning text is subject to output inspection when represented as message string fields.

## Model selection

By default the configured model replaces the client's requested model. `--preserve-model` honors client selection within approvals. Always approve the actual main, local, and fallback model names in the policy. A model approval is an exact string match.

Provider integrations currently select `nvidia`, `openai`, or `ollama`. Explicit endpoint and model settings override the selected provider's defaults; test other compatible endpoints against the request and response constraints above.
