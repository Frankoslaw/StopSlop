---
title: Agent permissions
description: Authorize tool, MCP, and memory operations with deny-by-default capability rules.
sidebar:
  order: 3
---

Agent authorization is separate from text scanning. It checks an authenticated identity against a capability resource and operation immediately before your integration executes a callback.

## Configure identities

```dotenv
STOPSLOP_ACCESS_TOKENS={"demo-agent":"replace-with-a-unique-secret"}
STOPSLOP_CLIENT_TOKEN=replace-with-a-unique-secret
```

Each client identity needs a unique nonempty bearer token. The caller cannot choose an identity in the authorization body. An unauthenticated loopback chat gateway still cannot authorize agent operations without a configured identity.

## Define permissions

```toml
[permissions]

[[permissions.rules]]
name = "approved_search"
type = "tool"
resource = "search"
operations = ["call"]
clients = ["demo-agent"]
action = "allow"

[[permissions.rules]]
name = "project_memory"
type = "memory"
resource = "project/*"
operations = ["read", "write"]
clients = ["demo-agent"]
action = "allow"

[[permissions.rules]]
name = "protect_secrets"
type = "memory"
resource = "project/secrets*"
operations = ["read", "write", "delete"]
clients = ["*"]
action = "deny"
```

Missing matches deny. Any matching deny wins over allow. Resource matching is case-sensitive shell-style matching (`fnmatchcase`); client matching uses an exact identity or `*`. Names identify logical capabilities rather than filesystem paths.

| Type | Operations | Example resource |
| --- | --- | --- |
| `tool` | `call` | `search` |
| `mcp` | `call`, `read` | `docs/search`, `docs/handbook` |
| `memory` | `read`, `write`, `delete` | `project/plan` |

## Wrap trusted callbacks

```python
import os
from stopslop import AgentGuard

guard = AgentGuard(
    os.environ["STOPSLOP_CLIENT_TOKEN"],
    base_url="https://your-gateway.example",
)

def search(query):
    return {"query": query, "results": []}

result = guard.call_tool("search", search, "public product documentation")
```

Other wrappers follow the same boundary:

```python
# Supply your trusted callbacks and memory store.
result = guard.call_mcp("docs", "search", mcp_search, query)
resource = guard.read_mcp("docs", "handbook", mcp_read)
value = guard.read_memory("project", "plan", memory_store.get)
guard.write_memory("project", "plan", value, memory_store.set)
guard.delete_memory("project", "plan", memory_store.delete)
```

Memory callbacks receive the key; writes receive the key and value. Tool and MCP wrappers forward your supplied arguments. For local enforcement, construct `AgentGuard(token, policy=policy)` instead; exactly one of `policy` or `base_url` is required.

## Integrate safely

Map approved names to a trusted callback registry and route every protected operation through the guard. Do not let an untrusted caller map a permitted resource to arbitrary executable code or arbitrary filesystem paths. The guard does not scan tool arguments, sandbox callbacks, or intercept unrelated direct calls.

Authorization rejects resource names with traversal segments, empty segments, backslashes, control characters, or more than 512 characters. Suspended identities are denied before execution. See [HTTP reference](../../reference/http/) for `/v1/authorize`.
