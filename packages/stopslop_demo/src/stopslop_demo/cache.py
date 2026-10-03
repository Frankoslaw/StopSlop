"""Local scenario response + policy audit cache, separate from the policy engine."""
from dataclasses import asdict
import hashlib
import json
import logging
from pathlib import Path
import uuid

import httpx

logger = logging.getLogger("stopslop.audit")


def fingerprint(settings):
    from stopslop import policy
    config = asdict(settings)
    for name in ("main_key", "fallback_key", "local_key", "jev_key"):
        config[name] = hashlib.sha256(config[name].encode()).hexdigest()
    files = list(Path(policy.__file__).parent.glob("*.py"))
    files += [Path(policy.__file__).parent / "default_rules.json"]
    files += [Path(__file__), Path(__file__).with_name("scenarios.py")]
    files += [Path(value) for value in (settings.policy_file, settings.rules_file) if value]
    source = {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    return hashlib.sha256(json.dumps({"version": 2, "config": config, "source": source}, sort_keys=True).encode()).hexdigest()


class AuditCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append({"level": record.levelno, "message": record.getMessage(),
                             "extra": {key: getattr(record, key) for key in ("progress", "rule_check") if hasattr(record, key)}})


class DemoCache(httpx.BaseTransport):
    def __init__(self, upstream, directory, namespace):
        self.upstream = upstream
        self.directory = Path(directory)
        self.namespace = namespace

    def handle_request(self, request):
        key = hashlib.sha256(self.namespace.encode() + request.method.encode()
                             + str(request.url).encode() + request.read()).hexdigest()
        path = self.directory / (key + ".json")
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached["version"] != 2:
                raise ValueError("Unknown cache format")
            if (type(cached["status"]) is not int or not isinstance(cached["body"], str)
                    or not isinstance(cached["headers"], dict) or not isinstance(cached["audit"], list)
                    or any(not isinstance(record, dict) or type(record.get("level")) is not int
                           or not isinstance(record.get("message"), str) for record in cached["audit"])):
                raise ValueError("Invalid cache entry")
            response = httpx.Response(cached["status"], content=cached["body"].encode(), headers=cached["headers"])
            logger.info("cache=hit replaying saved policy results and response; no backend calls")
            for record in cached["audit"]:
                logger.log(record["level"], "[cached] %s", record["message"], extra=record.get("extra", {}))
            return response
        except (OSError, ValueError, KeyError, TypeError):
            pass
        logger.info("cache=miss running policies and chat backend")
        capture = AuditCapture()
        logger.addHandler(capture)
        try:
            response = self.upstream.handle_request(request)
            body = response.read()
        finally:
            logger.removeHandler(capture)
        # Persist successes and policy blocks, never transient failures.
        blocked = valid_completion = False
        try:
            parsed = json.loads(body)
            if response.status_code == 403:
                blocked = parsed["error"]["code"] == "policy_blocked"
            elif response.is_success:
                valid_completion = isinstance(parsed["choices"][0]["message"], dict)
        except (ValueError, KeyError, TypeError, IndexError):
            pass
        if valid_completion or blocked:
            data = {"version": 2, "status": response.status_code, "body": body.decode(),
                    "headers": {name: value for name, value in response.headers.items()
                                if name.lower() in ("content-type", "x-stopslop-risks", "x-stopslop-action", "x-stopslop-rules")},
                    "audit": capture.records}
            temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(data), encoding="utf-8")
                temporary.replace(path)
            except OSError:
                logger.warning("Cache could not be saved; response is still available")
            finally:
                temporary.unlink(missing_ok=True)
        return response

    def close(self):
        self.upstream.close()
