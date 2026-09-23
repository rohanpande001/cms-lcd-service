# Deploying CMS LCD DX Lookup

Self-contained deploy: **no database, no external services, no API keys required.**
The reference data ships inside this archive (the CSV files at the root); the app
reads them into memory at startup and is fully functional on first boot.

## Prerequisites

- **Python 3.11** (3.14 is incompatible with the pinned `pydantic-core`; 3.11.x recommended)
  - macOS: `brew install python@3.11` or `pyenv install 3.11.8`
  - Linux: `sudo apt install python3.11 python3.11-venv` (Debian/Ubuntu)
- ~100 MB free disk, port **8000** free

## Quick start (any OS)

```bash
unzip cms-lcd-service-deploy-*.zip && cd cms-lcd-service

python3.11 -m venv venv311
./venv311/bin/pip install -r requirements.txt

# Run (development / first run)
./venv311/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open:
- **GUI**: http://localhost:8000/ui
- **API docs**: http://localhost:8000/docs
- **Health**: http://localhost:8000/health

Smoke test:

```bash
curl "http://localhost:8000/v1/lcd/dx-codes?cpt_code=J1568&state=NY" | head -c 300
# expect 307 dx codes, qualified article 59105
```

## Run as a service (persistent)

**macOS** (launchd — starts at login, auto-restarts):

```bash
make install-service
make service-status
```

**Linux** (systemd example):

```ini
# /etc/systemd/system/cmslcd.service
[Unit]
Description=CMS LCD DX Lookup
After=network.target

[Service]
WorkingDirectory=/opt/cms-lcd-service
ExecStart=/opt/cms-lcd-service/venv311/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
User=YOUR_USER

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now cmslcd
```

**Anywhere** (quick and dirty): `nohup ./venv311/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 &`

## Network access notes

- The **DX endpoints work fully offline** — no network calls.
- `GET /v1/lcd/coverage` (full article data) calls `api.coverage.cms.gov` and fetches
  a 60-minute Bearer token automatically (no credentials, CMS license endpoints).
  If that domain is blocked, every other feature still works.
- Outbound HTTPS to `downloads.cms.gov` + `api.coverage.cms.gov` is only needed for
  **data refresh**, not for running.

## Refreshing the CMS data

Data ships as of the zip date. To refresh (requires Python 3.11 venv + outbound HTTPS):

```bash
make refresh-mapping   # downloads CMS bulk + API, rebuilds all CSVs
make restart-service   # reload the data (or restart however you run it)
```

## Layout

```
app/                  FastAPI application (routers, services, core, static GUI)
scripts/              data build scripts + launchd template
*.csv                 reference data (the de-facto database) — do not hand-edit
docs/                 Notion documentation (cms-lcd-app-notion.md) — import into Notion
logs/                 created at runtime (audit + server logs)
Makefile              run / refresh / service targets
requirements.txt      pinned dependencies
README.md             full developer documentation
DEPLOY.md             this file
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `pydantic-core` import error | Wrong Python version — use 3.11 for the venv |
| Port 8000 busy | `lsof -ti :8000 \| xargs kill` or change `--port` |
| `/v1/lcd/coverage` 503 | CMS API unreachable/blocked — DX endpoints unaffected |
| Stale data | `make refresh-mapping && make restart-service` |
