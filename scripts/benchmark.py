"""Reproducible HTTP workload; creates temporary holds and cancels those it owns."""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import platform
import statistics
import threading
import time
import uuid
import requests

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="http://127.0.0.1:8000")
parser.add_argument("--mode", choices=["reads", "mixed", "contention"], default="reads")
parser.add_argument("--requests", type=int, default=200)
parser.add_argument("--concurrency", type=int, default=10)
parser.add_argument("--event-id")
parser.add_argument("--username", default="demo")
parser.add_argument("--password", default=os.getenv("SEATLOCK_PASSWORD"))
parser.add_argument("--output", default="artifacts/benchmark.json")
args = parser.parse_args()
if args.requests < 1 or args.concurrency < 1:
    parser.error("requests and concurrency must be positive")
base = args.base_url.rstrip("/")
session = requests.Session()
token = None
if args.mode != "reads":
    if not args.password:
        parser.error("Set SEATLOCK_PASSWORD or pass --password for write workloads.")
    csrf = session.get(base + "/api/auth/csrf", timeout=10).json()["csrfToken"]
    response = session.post(
        base + "/api/auth/login",
        json={"username": args.username, "password": args.password},
        headers={"X-CSRFToken": csrf},
        timeout=15,
    )
    response.raise_for_status()
    token = response.json()["access"]
event_id = args.event_id or session.get(base + "/api/events", timeout=10).json()["results"][0]["id"]
available = [
    s["id"]
    for s in session.get(base + f"/api/events/{event_id}/seats", timeout=10).json()["seats"]
    if s["status"] == "AVAILABLE"
]
if args.mode != "reads" and not available:
    parser.error("No available seat in selected event")
if args.mode == "mixed" and len(available) < (args.requests + 4) // 5:
    parser.error(
        "Mixed run needs one available seat per five requests; reduce --requests or seed more seats."
    )
if args.mode == "contention":
    args.requests = args.concurrency
barrier = threading.Barrier(args.concurrency) if args.mode == "contention" else None


def run(i):
    client = requests.Session()
    headers = {"Authorization": "Bearer " + token} if token else {}
    write = args.mode == "contention" or (args.mode == "mixed" and i % 5 == 0)
    endpoint = "/api/reservations" if write else ("/api/events" if i % 2 else f"/api/events/{event_id}/seats")
    if barrier:
        barrier.wait(timeout=30)
    started = time.perf_counter()
    reservation_id = None
    try:
        if write:
            seat_id = available[0] if args.mode == "contention" else available[i // 5]
            response = client.post(
                base + endpoint,
                json={"seat_id": seat_id},
                headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                timeout=30,
            )
        else:
            response = client.get(base + endpoint, timeout=30)
        elapsed = (time.perf_counter() - started) * 1000
        status = str(response.status_code)
        if response.status_code == 201:
            reservation_id = response.json()["id"]
        return endpoint, status, elapsed, reservation_id
    except requests.RequestException:
        return endpoint, "network_error", (time.perf_counter() - started) * 1000, None
    finally:
        client.close()


started = time.perf_counter()
with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
    rows = list(pool.map(run, range(args.requests)))
duration = time.perf_counter() - started
groups = defaultdict(list)
for endpoint, status, elapsed, _ in rows:
    groups[endpoint + " " + status].append(elapsed)


def percentile(values, p):
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, int(len(ordered) * p + 0.999999) - 1))], 2)


report = {
    "mode": args.mode,
    "requests": args.requests,
    "concurrency": args.concurrency,
    "duration_seconds": round(duration, 3),
    "throughput_rps": round(len(rows) / duration, 2),
    "status_counts": dict(Counter(row[1] for row in rows)),
    "platform": platform.platform(),
    "cpu_count": os.cpu_count(),
    "python": platform.python_version(),
    "event_id": event_id,
    "initial_available_seats": len(available),
    "note": "Single local client; no warmup. Cleanup excluded. Authentication occurs before timing.",
    "latency_ms": {
        key: {
            "count": len(values),
            "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95),
            "p99": percentile(values, 0.99),
            "mean": round(statistics.mean(values), 2),
        }
        for key, values in groups.items()
    },
}
# Contention cleanup MUST happen after all requests finish, never during the measured race.
cleanup_errors = []
for _, _, _, reservation_id in rows:
    if reservation_id:
        response = session.post(
            base + f"/api/reservations/{reservation_id}/cancel",
            headers={"Authorization": "Bearer " + token},
            timeout=15,
        )
        if response.status_code != 200:
            cleanup_errors.append(reservation_id)
report["cleanup_errors"] = cleanup_errors
path = Path(args.output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
if args.mode == "contention":
    assert report["status_counts"] == {"409": args.requests - 1, "201": 1} or report["status_counts"] == {
        "201": 1,
        "409": args.requests - 1,
    }, "Contention invariant failed; inspect the report."
