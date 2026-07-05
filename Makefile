VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: install run run-debug fetch-assets clean

$(VENV)/bin/activate:
	python3 -m venv $(VENV)

install: $(VENV)/bin/activate
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

run: install
	$(PYTHON) main.py

run-debug: install
	LOG_LEVEL=DEBUG $(PYTHON) main.py

fetch-assets: install
	$(PYTHON) fetch_assets.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
	rm -rf $(VENV)
