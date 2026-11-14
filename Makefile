PYTHON ?= python
CONFIRM ?=

.PHONY: dev stack probe seed test lint eval bench scale demo reset

dev:
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 18000

stack:
	docker compose up -d --build

probe:
	$(PYTHON) scripts/probe.py

seed:
	$(PYTHON) scripts/seed.py

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check app catalog evals scripts tests

eval:
	$(PYTHON) evals/run.py --reps 3 --rounds 5

bench:
	$(PYTHON) scripts/bench.py

scale:
	$(PYTHON) scripts/scale.py

demo:
	$(PYTHON) scripts/demo_check.py

reset:
	$(PYTHON) scripts/reset.py $(CONFIRM)
