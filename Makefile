UV ?= uv

.PHONY: test demo clean

test:
	$(UV) run pytest -q

demo:
	$(UV) run --package stopslop_demo stopslop-demo --scenario nda --policy-file policy.json --color always --max-tokens 96 --timeout 45

clean:
	$(UV) run python tools/demo_tasks.py clean
