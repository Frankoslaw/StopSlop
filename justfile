uv := env_var_or_default("UV", "uv")

test:
    {{uv}} run --no-sync pytest -q

# Demo uses the single provider/classifier configuration from .env.
demo *args:
    {{uv}} run --no-sync --package stopslop-demo stopslop-demo --scenario nda --color always --max-tokens 96 {{args}}

# Local classifier and Ollama chat; override the configured model with --model NAME.
demo-local *args:
    {{uv}} run --no-sync --package stopslop-demo stopslop-demo --scenario nda --provider ollama --base-url http://127.0.0.1:11434/v1 --key ollama --classifier laya --color always --max-tokens 96 {{args}}

proxy *args:
    {{uv}} run --no-sync --package stopslop-proxy stopslop-proxy {{args}}

top:
    {{uv}} run --no-sync --package stopslop-top stopslop-top

# Stop running processes before resetting local runtime state.
clean:
    {{uv}} run --no-sync python tools/clean_state.py
