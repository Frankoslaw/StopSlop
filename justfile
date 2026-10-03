uv := env_var_or_default("UV", "uv")

test:
    {{uv}} run --no-sync pytest -q

# Fast hosted demo: Jev classification and NVIDIA chat.
demo *args:
    {{uv}} run --no-sync --package stopslop_demo stopslop-demo --scenario nda --policy-file policy.toml --main-provider nvidia --classifier jev --color always --max-tokens 96 --timeout 45 {{args}}

# Local classifier and Ollama chat; override the configured model with --model NAME.
demo-local *args:
    {{uv}} run --no-sync --package stopslop_demo stopslop-demo --scenario nda --policy-file policy.toml --main-provider ollama --classifier laya --color always --max-tokens 96 --timeout 120 {{args}}

top:
    {{uv}} run --no-sync --package stopslop_top stopslop-top

# Stop running processes before resetting local runtime state.
clean:
    {{uv}} run --no-sync python tools/clean_state.py
