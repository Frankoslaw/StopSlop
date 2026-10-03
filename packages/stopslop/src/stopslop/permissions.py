"""Deny-by-default operation permissions for authenticated agent integrations."""
from fnmatch import fnmatchcase

OPERATIONS = {"tool": {"call"}, "mcp": {"call", "read"}, "memory": {"read", "write", "delete"}}


def validate_permissions(data):
    if not isinstance(data, dict) or set(data) - {"rules"} or not isinstance(data.get("rules", []), list):
        raise ValueError("permissions must contain a rules array")
    ids = set()
    for rule in data.get("rules", []):
        if (not isinstance(rule, dict) or set(rule) != {"id", "kind", "resource", "operations", "clients", "action"}
                or not isinstance(rule["id"], str) or not rule["id"] or rule["id"] in ids
                or not isinstance(rule["kind"], str) or rule["kind"] not in OPERATIONS
                or not isinstance(rule["resource"], str) or not rule["resource"]
                or rule["action"] not in ("allow", "deny")
                or not isinstance(rule["operations"], list) or not rule["operations"]
                or any(not isinstance(op, str) or op not in OPERATIONS[rule["kind"]] for op in rule["operations"])
                or not isinstance(rule["clients"], list) or not rule["clients"]
                or any(not isinstance(c, str) or not c for c in rule["clients"])):
            raise ValueError("Invalid permission rule")
        ids.add(rule["id"])
    return data.get("rules", [])


def authorize_operation(policy, definition, client_id, kind, resource, operation):
    from .policy import PolicyError
    if not client_id or client_id not in policy.tokens:
        raise PolicyError("unauthorized_client", 401)
    policy.runtime.authorize_client(client_id)
    if (not isinstance(kind, str) or kind not in OPERATIONS or not isinstance(operation, str)
            or operation not in OPERATIONS[kind] or not isinstance(resource, str)
            or not resource or len(resource) > 512 or "\\" in resource
            or any(segment in (".", "..", "") for segment in resource.split("/"))
            or any(ord(char) < 32 for char in resource)):
        raise PolicyError("invalid_operation", 400)
    matches = [r for r in (definition.permissions if definition else [])
               if r["kind"] == kind and operation in r["operations"]
               and (client_id in r["clients"] or "*" in r["clients"])
               and fnmatchcase(resource, r["resource"])]
    allowed = bool(matches) and not any(r["action"] == "deny" for r in matches)
    ids = [r["id"] for r in matches]
    policy.runtime.audit("operation_authorized" if allowed else "operation_denied",
                         client_id=client_id, kind=kind, resource=resource, operation=operation, rules=ids)
    if not allowed:
        policy.runtime.violation("operation_denied", ids, client_id=client_id,
                                 direction="operation", action="block", kind=kind, resource=resource, operation=operation)
        raise PolicyError("operation_denied", 403, ids)
    return {"allowed": True, "client_id": client_id, "rules": ids}


class AgentGuard:
    """Check immediately before executing a tool/MCP callback or accessing memory.

    Supply a Policy for local enforcement or a gateway URL for remote agents.
    Resource names identify registered capabilities, not caller-chosen filesystem paths.
    """
    def __init__(self, token, *, policy=None, base_url=None, transport=None, timeout=10):
        if (policy is None) == (base_url is None):
            raise ValueError("Supply exactly one of policy or base_url")
        self.token, self.policy = token, policy
        self.base_url, self.transport, self.timeout = base_url, transport, timeout

    def authorize(self, kind, resource, operation):
        if self.policy:
            client_id = self.policy.authenticate("Bearer " + self.token)
            return self.policy.authorize_operation(client_id, kind, resource, operation)
        import httpx
        from .policy import PolicyError
        with httpx.Client(transport=self.transport, timeout=self.timeout, follow_redirects=False) as client:
            response = client.post(self.base_url.rstrip("/") + "/v1/authorize",
                                   headers={"Authorization": "Bearer " + self.token},
                                   json=dict(kind=kind, resource=resource, operation=operation))
        if not response.is_success:
            raise PolicyError("operation_denied" if response.status_code == 403 else "authorization_unavailable",
                              response.status_code)
        result = response.json()
        if not isinstance(result, dict) or result.get("allowed") is not True:
            raise PolicyError("authorization_unavailable", 503)
        return result

    def call_tool(self, name, callback, *args, **kwargs):
        self.authorize("tool", name, "call")
        return callback(*args, **kwargs)

    def call_mcp(self, server, tool, callback, *args, **kwargs):
        self.authorize("mcp", server + "/" + tool, "call")
        return callback(*args, **kwargs)

    def read_mcp(self, server, resource, callback, *args, **kwargs):
        self.authorize("mcp", server + "/" + resource, "read")
        return callback(*args, **kwargs)

    def read_memory(self, store, key, callback):
        self.authorize("memory", store + "/" + key, "read")
        return callback(key)

    def write_memory(self, store, key, value, callback):
        self.authorize("memory", store + "/" + key, "write")
        return callback(key, value)

    def delete_memory(self, store, key, callback):
        self.authorize("memory", store + "/" + key, "delete")
        return callback(key)
