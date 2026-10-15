# ECMP

ECMP is an energy community management platform. It provides a FastAPI backend for managing communities, members, service points, meters, assets, allocations, flexibility, forecasts, pricing, and operational status. The repository also includes a LiveOps service for ingesting MQTT telemetry and a Grafana dashboard for monitoring.

## Repository layout

| Path | Purpose |
| --- | --- |
| `ecmp/` | Main FastAPI API, database models, Alembic migrations, optimisation logic, and scheduled data jobs. |
| `liveops/` | FastAPI service for real-time MQTT telemetry and community status. |
| `db/` | PostgreSQL/TimescaleDB database initialisation script. |
| `clickhouse/` | ClickHouse configuration and schema initialisation. |
| `keycloak/` | Keycloak realm export. |
| `grafana/` | Grafana provisioning and dashboard definitions. |
| `postman/` | Postman API collection. |
| `docker-compose.yml` | Main API and Grafana stack. |
| `local-docker-compose.yml` | Local supporting-services stack (database, Keycloak, ClickHouse, and Grafana). |

## Prerequisites

- Docker and Docker Compose, for containerised development
- Python 3.14, for running the services directly
- Access to the required PostgreSQL, Keycloak, MQTT, and external data-service credentials

## Configuration

Copy the example environment file and populate it with local credentials and connection details:

```powershell
Copy-Item .env.example .env
```
Do not commit certificates or production credentials.

## Run with Docker

Start the API and Grafana stack:

```powershell
docker compose up --build
```

The main API is exposed on `http://localhost:8000`; FastAPI's interactive documentation is available at `http://localhost:8000/docs`. Grafana is available at `http://localhost:3000` (the compose configuration supplies the default `admin` / `secret` credentials).

To start the local supporting services, use:

```powershell
docker compose -f local-docker-compose.yml up -d
```

This exposes PostgreSQL on port `5432`, Keycloak on `8081`, ClickHouse HTTP on `8123`, ClickHouse native protocol on `9000`, and Grafana on `3000`.

## Run services locally

Install dependencies for the main API and start Uvicorn:

```powershell
Set-Location ecmp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Run LiveOps separately on port 8001:

```powershell
Set-Location liveops
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

## Database migrations

Alembic configuration and migrations live under `ecmp/alembic`. With the ECMP environment configured, apply migrations from the `ecmp` directory:

```powershell
alembic upgrade head
```

## Testing

Run the existing test suite from the `ecmp` directory:

```powershell
pytest
```

## API areas

The main API exposes routers for members, allocations, service points, grid elements, meters, channels, assets, simulations, flexibility, forecasting, pricing, and status. Most protected endpoints require a Keycloak access token with the appropriate role.

The Postman collection is available at `postman/ECMP.postman_collection.json`.

## License

This project is licensed under the [Apache License, Version 2.0](LICENSE).
