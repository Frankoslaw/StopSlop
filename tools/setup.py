"""Prepare a local Ollama + Laya checkout using only the Python standard library."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

MODEL = "qwen3:0.6b"
OLLAMA_URL = "http://127.0.0.1:11434"


def ollama_executable():
    executable = shutil.which("ollama")
    if executable:
        return executable
    # The Windows installer can be available before a terminal refreshes PATH.
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
    if candidate.is_file():
        return str(candidate)
    raise ValueError("Install Ollama from https://ollama.com/download, then run just setup again.")


def installed_models():
    with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=2) as response:
        return json.load(response)["models"]


def ensure_server(executable, root):
    try:
        installed_models()
        return None
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    environment = {**os.environ, "OLLAMA_HOST": OLLAMA_URL}
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    with (root / "stopslop.log").open("ab") as log:
        process = subprocess.Popen([executable, "serve"], stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, env=environment, **options)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            installed_models()
            return process
        except (urllib.error.URLError, TimeoutError, OSError):
            if process.poll() is not None:
                break
            time.sleep(0.25)
    if process.poll() is None:
        process.terminate()
    raise ValueError("Ollama did not start. Check stopslop.log or run just ollama-serve.")


def write_environment(root, models):
    model = next((entry["name"] for entry in models if entry.get("name") == MODEL), None)
    if model is None:
        raise ValueError(f"Ollama did not report {MODEL} after pulling it; .env was preserved.")
    values = {
        "PROVIDER": "ollama", "BASE_URL": OLLAMA_URL + "/v1", "MODEL": model, "KEY": "ollama",
        "CLASSIFIER": "laya", "FALLBACK_CLASSIFIER": "ollama", "JEV_KEY": "",
        "LOCAL_BASE_URL": OLLAMA_URL + "/v1", "LOCAL_MODEL": model, "LOCAL_KEY": "ollama",
        "POLICY_FILE": "policy.toml", "AUTOGEN": "true",
    }
    text = (root / ".env.example").read_text(encoding="utf-8")
    for key, value in values.items():
        name = "STOPSLOP_" + key
        pattern = rf"(?m)^{name}=.*$"
        text = re.sub(pattern, lambda _: f"{name}={value}", text) if re.search(pattern, text) else text + f"\n{name}={value}\n"
    temporary = root / ".env.setup.tmp"
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(root / ".env")
    finally:
        temporary.unlink(missing_ok=True)


def setup(root, uv="uv"):
    root = Path(root).resolve()
    executable = ollama_executable()
    subprocess.run([uv, "--cache-dir", str(root / ".uv-cache"), "sync", "--all-packages", "--extra", "laya"],
                   cwd=root, check=True)
    process = ensure_server(executable, root)
    try:
        subprocess.run([executable, "pull", MODEL], cwd=root, check=True,
                       env={**os.environ, "OLLAMA_HOST": OLLAMA_URL})
        write_environment(root, installed_models())
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
        raise
    print("Local setup complete: qwen3:0.6b chat, Laya CPU assessment, Ollama fallback and deferred autogen.")
    print("Run just demo-local. Run just top in another terminal.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    try:
        setup(Path(__file__).resolve().parents[1], args.uv)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Setup failed: {error}\n")
