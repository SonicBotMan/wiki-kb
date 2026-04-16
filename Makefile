.PHONY: test lint pre-push

test:
	python -m pytest tests/ -v --tb=short

lint:
	@for f in scripts/*.py; do \
		echo "  Checking $$f..."; \
		python -m py_compile "$$f" || exit 1; \
	done
	@echo "✅ Lint passed (all scripts/*.py)"

pre-push: lint test
	@echo "✅ All checks passed — safe to push"
