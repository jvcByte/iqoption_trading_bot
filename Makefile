.PHONY: install run test clean

install:
	pip install -r requirements.txt

run:
	python main.py

run-debug:
	LOG_LEVEL=DEBUG python main.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
