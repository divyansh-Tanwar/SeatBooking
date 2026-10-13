# Validation report — 9 October 2026

## Verified
- PostgreSQL integration suite: **31 passed in 24.95 seconds**, no warnings in final run.
- Ruff: all checks passed.
- Django system checks: no issues.
- Migration drift check: no changes detected.
- Dependency check: no broken requirements.
- JavaScript syntax check: passed.
- Browser automation in headless Microsoft Edge: event browsing, login, holding a seat, second-browser visibility, reload restoration, background payment completion, booking history, release, mobile overflow check, and no JavaScript page errors.
- Desktop and mobile screenshots were inspected.

The concurrency suite uses independent database connections and synchronized starts. It includes 100 API clients competing for one seat. Database uniqueness, lock-wait expiration, duplicate callbacks, request replay, refresh replay, stale expiry jobs, and outbox recovery are verified.

## Real HTTP workloads
Environment: Windows 10, AMD Ryzen 7 5800H (8 cores / 16 logical processors), Python 3.12.3, Django 5.2.18, PostgreSQL 17.11, one Daphne application process, 160 maximum database connections. Database connection reuse is disabled (CONN_MAX_AGE=0). Database and load generator run on the same laptop.

Dataset: three events with 60 seats each; the measured event had 59 available seats after the browser test. Authentication happens before timing. No warmup. Background job runner was active. The local preview uses five-second polling and a direct outbox runner, rather than Redis/Celery. Debug mode and request logging are enabled.

| Workload | Requests / concurrency | Outcome | p95 latency |
|---|---:|---|---:|
| Read-only | 200 / 10 | 200 successful responses | Events 189.38 ms; seats 205.52 ms |
| Mixed: 80 reads, 20 distinct-seat reservations | 100 / 10 | All succeeded | Reservations 223.39 ms; events 196.30 ms; seats 195.79 ms |
| Same-seat contention | 100 / 100 | **1 created, 99 conflicts** | Conflicts 1484.32 ms |

Throughput was 87.94 requests/s for reads and 77.52 requests/s for the mixed workload. The sole successful contention request took 295.60 ms; a percentile based on one sample is not meaningful. There were no cleanup errors.

Raw JSON reports are in [benchmarks](benchmarks/). These short local runs demonstrate behavior and provide a baseline; they do not establish production capacity or a sustained service-level objective.

## Boundaries
- Docker is absent, so the Compose image/services and GitHub Actions workflow were not executed here.
- Real Redis transport and Celery broker integration have not been exercised locally. WebSocket consumer/reconnection tests use the Channels in-memory layer. Celery task functions and leased outbox behavior are exercised against real PostgreSQL.
- The running Windows preview uses actual PostgreSQL and the same booking/payment services, with a development-only job runner and browser polling.
- Email is captured through the console in the local preview. Compose is configured for Mailpit.
- HTTPS, production SMTP, external payment providers and multi-host deployment are outside this local validation.
- Full expiration lifecycle tests manipulate deadlines and exercise lock waits; the browser test checks reload recovery, not a five-minute wall-clock wait.

## Run it here
The prepared local environment is available at http://127.0.0.1:8000.

From the project directory:
```powershell
.\scripts\start-local.ps1
.\scripts\stop-local.ps1
```

Stop before starting a second instance. Local PostgreSQL binds only to loopback port 55432 and uses trust authentication for isolated development data under artifacts/pgdata. Do not use that cluster as a production database.

For a fresh machine, use the Docker instructions in [README](../README.md).
