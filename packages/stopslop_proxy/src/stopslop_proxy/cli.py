import argparse
import logging
import uvicorn
import structlog
from stopslop.config import Settings, add_settings_arguments
from .app import create_app


def main():
    parser = argparse.ArgumentParser(description="Run the StopSlop text chat proxy")
    add_settings_arguments(parser)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--json-logs", action="store_true")
    args = vars(parser.parse_args())
    json_logs = args.pop("json_logs")
    structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()])
    host, port, env_file = args.pop("host"), args.pop("port"), args.pop("env_file")
    try:
        settings = Settings.load(env_file, **args)
        if not settings.key:
            raise ValueError("Set STOPSLOP_KEY in .env or the environment")
        import ipaddress
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if not loopback and not settings.access_tokens:
            raise ValueError("A non-loopback gateway requires STOPSLOP_ACCESS_TOKENS")
        from stopslop.preflight import check_semantic_setup
        check_semantic_setup(settings)
        app = create_app(settings)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    uvicorn.run(app, host=host, port=port)
