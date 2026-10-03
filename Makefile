UV ?= uv

.PHONY: test demo top

test:
	$(UV) run --no-sync pytest -q

demo:
	$(UV) run --no-sync --package stopslop_demo stopslop-demo --scenario nda --policy-file policy.toml --color always --max-tokens 96 --timeout 45 --log-chats

top:
	$(UV) run --no-sync --package stopslop slopstop-top
