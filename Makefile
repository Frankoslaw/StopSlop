UV ?= uv

.PHONY: test demo top

test:
	$(UV) run pytest -q

demo:
	$(UV) run --package stopslop_demo stopslop-demo --scenario nda --policy-file policy.json --color always --max-tokens 96 --timeout 45

top:
	$(UV) run --package stopslop slopstop-top
