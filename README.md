# SeatLock
A concurrent ticket-booking platform built with Django, PostgreSQL, Channels, Redis and Celery.

SeatLock offers event browsing, a live seat map, five-minute reservations, simulated checkout, booking history, JWT access tokens with rotating refresh credentials, and organizer APIs. No card details or real payments are collected.

## Prepared Windows preview
This machine has a working preview at http://127.0.0.1:8000. It uses isolated PostgreSQL, browser polling and a development background runner because Docker/Redis are not installed.

Restart from this folder with:
```powershell
.\scripts\stop-local.ps1
.\scripts\start-local.ps1
```

The scripts use the runtime prepared under artifacts/ and .venv/. For a fresh checkout, follow the Docker setup below. Read [the measured validation report](docs/VALIDATION.md) for results and limits.

## Quick start with Docker
Install Docker Desktop with Linux containers first. From this directory in PowerShell:

```powershell
Copy-Item .env.example .env
docker compose build
docker compose up -d db redis mail
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py seed_demo
docker compose up -d web worker beat
```

Open http://localhost:8000. Mail capture is at http://localhost:8025.

Demo accounts (development only):
- Customer: `demo`
- Organizer: `organizer`
- Password for both: `SeatLock-demo-2026!`

The application defaults to development settings. Before any public deployment use separate random secrets, `DEBUG=0`, secure cookies, HTTPS, correct allowed hosts/CSRF origins, and ingress rate limits. Demo seed refuses production mode.

## Tests
The suite needs PostgreSQL. SQLite is deliberately not accepted by booking services.

```powershell
docker compose exec db psql -U seatlock -d seatlock -c "ALTER SYSTEM SET max_connections = '160';"
docker compose restart db
docker compose run --rm web pytest -q
docker compose run --rm web python manage.py makemigrations --check --dry-run
docker compose run --rm web ruff check .
```

Wait for the database health check after restarting. The 100-request test opens independent connections; allow at least 110 connections (160 recommended).

Tests cover competing HTTP requests, database uniqueness, request retries, duplicate callbacks, expiration, cancellation races, replacement holds, refresh replay, ownership, CSRF, outbox retries, and WebSocket reconnect.

## How correctness works
The existing event-seat row is the serialization point. Every inventory change locks that row before a reservation or payment attempt. Database constraints also prevent two HELD/CONFIRMED reservations for the same seat.

Reservation transitions are HELD → CONFIRMED / EXPIRED / CANCELLED. Expired records are transitioned under the seat lock before replacing them. Time is read from PostgreSQL after lock acquisition. Confirmation eligibility is decided at this point, not at the later commit timestamp.

API retries use customer-scoped idempotency keys. Business uniqueness applies even when callers use different keys. Payment events have unique identifiers and payload fingerprints.

Outbox records commit alongside booking changes. A leased dispatcher retries failures and recovers abandoned work. Delivery can repeat; consumers tolerate duplicates. Email can be duplicated after a crash. Live events are hints, and clients periodically reconcile through the API.

Read [the architecture](docs/ARCHITECTURE.md) and [API guide](docs/API.md).

## Benchmarks
Run these against a seeded local environment. Use a fresh available seat for contention; the script releases holds after the workload completes.

```powershell
$env:SEATLOCK_PASSWORD = 'SeatLock-demo-2026!'
python scripts/benchmark.py --mode reads --requests 200 --concurrency 10 --output artifacts/reads.json
python scripts/benchmark.py --mode mixed --requests 100 --concurrency 10 --output artifacts/mixed.json
python scripts/benchmark.py --mode contention --concurrency 100 --output artifacts/contention.json
```

Reports include workload, host, request count, duration, throughput, status groups, and p50/p95/p99. Successful writes and conflicts are reported separately. These are measurements of the stated local setup, not production capacity claims.

## Operational checks
- `GET /health/live`: process responds.
- `GET /health/ready`: database responds.
- `GET /api/organizer/operations`: broker reachability and pending/overdue outbox counts.
- Request logs contain JSON request IDs, paths, status and duration; credentials and request bodies are excluded.
- One Celery Beat instance. Workers can run concurrently.
- Database migrations run as a release step, not on every application startup.

Production: run `python manage.py collectstatic --noinput` before serving, use a TLS reverse proxy, configure SMTP, back up PostgreSQL, alert on overdue outbox work, and use a dedicated migration database role. Docker volumes retain database and Redis data. Never expose database/Redis ports publicly.

## Scope and limits
- One seat per reservation; confirmed bookings cannot be cancelled.
- Published inventory and event configuration are immutable through the API.
- Organizer creation is administrative; organizer management is API-based.
- One simulated payment attempt per reservation; failures require releasing the hold.
- Refresh replay revokes the entire session. A lost refresh response may require signing in again.
- Access revocation applies at the next authentication check; in-flight requests may complete.
- Browser access tokens live in memory. Refresh credentials use HttpOnly cookies plus CSRF protection.
- Auth throttling is process-local for this demo; enforce shared ingress limits before public deployment.
- Idempotency records and consumed refresh credentials are retained. Add a documented retention policy before sustained production use.
- Docker/Linux is the supported full-stack runtime. Local Windows validation is documented in the validation report.
