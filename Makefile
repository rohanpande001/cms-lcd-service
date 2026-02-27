VENV = venv311
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip
UVICORN = $(VENV)/bin/uvicorn

install:
	python3.11 -m venv $(VENV)
	$(PIP) install -r requirements.txt

run:
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --reload

run-prod:
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --workers 4

refresh-mapping:
	curl -L -o /tmp/current_article.zip \
		https://downloads.cms.gov/medicare-coverage-database/downloads/exports/current_article.zip
	cd /tmp && unzip -o current_article.zip current_article_csv.zip
	cd /tmp && unzip -o current_article_csv.zip article.csv article_x_hcpc_code.csv
	$(PYTHON) scripts/build_mapping.py
	@echo "article_hcpc_mapping.csv updated."
