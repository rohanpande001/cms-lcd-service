VENV = venv311
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip
UVICORN = $(VENV)/bin/uvicorn

# Persistent service (macOS launchd) — runs on port 8000, survives logout,
# auto-restarts on crash, starts at login.
SERVICE_PLIST = $(HOME)/Library/LaunchAgents/com.cmslcd.service.plist

install-service:
	@mkdir -p $(HOME)/Library/LaunchAgents
	@sed "s|__REPO__|$$(pwd)|g; s|__USER__|$$USER|g" scripts/launchd.plist.in > $(SERVICE_PLIST)
	launchctl bootout gui/$$(id -u)/com.cmslcd.service 2>/dev/null || true
	launchctl bootstrap gui/$$(id -u) $(SERVICE_PLIST) || launchctl load -w $(SERVICE_PLIST)
	@echo "Service installed and running at http://localhost:8000 (plist: $(SERVICE_PLIST))"

restart-service:
	launchctl kickstart -k gui/$$(id -u)/com.cmslcd.service
	@echo "Service restarted."

uninstall-service:
	launchctl bootout gui/$$(id -u)/com.cmslcd.service 2>/dev/null || launchctl unload $(SERVICE_PLIST) 2>/dev/null || true
	rm -f $(SERVICE_PLIST)
	@echo "Service removed."

service-status:
	launchctl print gui/$$(id -u)/com.cmslcd.service 2>/dev/null | grep -E "state|pid" || echo "not loaded"
	@curl -s http://localhost:8000/health && echo "" || echo "not responding"

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
	curl -L -o /tmp/ncd.zip \
		https://downloads.cms.gov/medicare-coverage-database/downloads/exports/ncd.zip
	cd /tmp && unzip -o current_article.zip current_article_csv.zip
	cd /tmp && unzip -o current_article_csv.zip article.csv article_x_hcpc_code.csv article_x_icd10_covered.csv article_x_icd10_noncovered.csv article_related_ncd_documents.csv article_x_contractor.csv
	cd /tmp && unzip -o ncd.zip ncd_csv.zip
	cd /tmp && unzip -o ncd_csv.zip ncd_trkg.csv
	$(PYTHON) scripts/build_article_ref.py
	$(PYTHON) scripts/build_icd10_index.py
	$(PYTHON) scripts/build_governing_map.py
	$(PYTHON) scripts/build_contractor_ref.py
	$(PYTHON) scripts/build_contractor_states.py
	$(PYTHON) scripts/build_state_reference.py
	$(PYTHON) scripts/build_mapping.py
	@echo "Mapping, ICD-10 index, governing map and jurisdiction files updated."
