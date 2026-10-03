import argparse
import logging
import uvicorn
import structlog
from .config import Settings, POLICIES
from .proxy import create_app


def main():
    parser = argparse.ArgumentParser(description="Run the StopSlop text chat proxy")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--policy", choices=POLICIES)
    for name in ("main-model", "main-base-url", "fallback-model", "fallback-base-url", "rules-file", "policy-file", "local-model", "local-base-url"):
        parser.add_argument("--" + name)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--deterministic", action="store_true", default=None)
    parser.add_argument("--log-file")
    parser.add_argument("--metrics-file")
    parser.add_argument("--preserve-model", action="store_true", default=None)
    parser.add_argument("--json-logs", action="store_true")
    args = vars(parser.parse_args())
    json_logs = args.pop("json_logs")
    structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()])
    host, port, env_file = args.pop("host"), args.pop("port"), args.pop("env_file")
    try:
        settings = Settings.load(env_file, **args)
        app = create_app(settings)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    uvicorn.run(app, host=host, port=port)
