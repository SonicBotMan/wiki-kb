.PHONY: test lint pre-push

test:
	PYTHONPATH=scripts python -m pytest tests/ -v --tb=short

lint:
	python -m py_compile scripts/wiki_mcp_server.py
	python -m py_compile scripts/wiki_utils.py
	@echo "✅ Lint passed"

pre-push: lint test
	@echo "✅ All checks passed — safe to push"
