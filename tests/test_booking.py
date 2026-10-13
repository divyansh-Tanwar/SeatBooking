from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import uuid
import pytest
from django.db import connections, transaction, IntegrityError
from django.utils import timezone
from rest_framework.test import APIClient
from seatlock.models import Reservation, Booking, PaymentAttempt, PaymentEvent, EventSeat, OutboxEvent
from seatlock.services import (
    reserve_seat,
    start_checkout,
    cancel_reservation,
    process_payment_event,
    expire_reservation,
    DomainError,
)

pytestmark = pytest.mark.django_db(transaction=True)


def payload(attempt, event_id="event-1", outcome="success"):
    return {
        "event_id": event_id,
        "attempt_id": str(attempt.id),
        "amount": attempt.amount,
        "currency": attempt.currency,
        "outcome": outcome,
    }


def hold(inventory, key="hold"):
    user, _, seat = inventory
    body, code = reserve_seat(user, seat.id, key)
    assert code == 201
    return Reservation.objects.get(id=body["id"])


def checkout(inventory):
    r = hold(inventory)
    body, code = start_checkout(inventory[0], r.id, "checkout")
    assert code == 202
    return r, PaymentAttempt.objects.get(id=body["attempt_id"])


def run_concurrently(functions):
    barrier = Barrier(len(functions))

    def run(fn):
        connections.close_all()
        try:
            barrier.wait(timeout=30)
            return fn()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        return list(pool.map(run, functions))


def test_100_http_requests_one_seat(inventory):
    user, _, seat = inventory

    def attempt():
        client = APIClient()
        client.force_authenticate(user)
        return client.post(
            "/api/reservations",
            {"seat_id": str(seat.id)},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        ).status_code

    results = run_concurrently([attempt] * 100)
    assert results.count(201) == 1
    assert results.count(409) == 99
    assert Reservation.objects.filter(seat=seat, status="HELD").count() == 1


def test_same_key_concurrently_replays_one_hold(inventory):
    results = run_concurrently([lambda: reserve_seat(inventory[0], inventory[2].id, "same")] * 8)
    assert all(code == 201 for _, code in results)
    assert len({body["id"] for body, _ in results}) == 1
    assert Reservation.objects.count() == 1


def test_different_payload_same_key_rejected(inventory):
    hold(inventory)
    other = EventSeat.objects.create(event=inventory[1], label="A02", price=100)
    with pytest.raises(DomainError, match="different inputs"):
        reserve_seat(inventory[0], other.id, "hold")


def test_database_blocks_double_allocation(inventory):
    r = hold(inventory)
    with pytest.raises(IntegrityError), transaction.atomic():
        Reservation.objects.create(
            customer=r.customer,
            seat=r.seat,
            status="HELD",
            price=r.price,
            currency=r.currency,
            created_at=r.created_at,
            expires_at=r.expires_at,
        )


def test_confirm_retries_and_duplicate_callback(inventory):
    r, attempt = checkout(inventory)
    first = start_checkout(inventory[0], r.id, "checkout")
    assert start_checkout(inventory[0], r.id, "checkout") == first
    results = run_concurrently([lambda: process_payment_event(payload(attempt))] * 8)
    assert all(result["status"] == "SUCCEEDED" for result in results)
    assert Booking.objects.filter(reservation=r).count() == 1
    assert PaymentEvent.objects.count() == 1
    assert PaymentAttempt.objects.count() == 1
    body, code = start_checkout(inventory[0], r.id, "different-key")
    assert code == 200 and body["booking_id"]


def test_callback_event_id_payload_mismatch(inventory):
    _, attempt = checkout(inventory)
    process_payment_event(payload(attempt))
    with pytest.raises(DomainError):
        process_payment_event(payload(attempt, outcome="failure"))
    assert Booking.objects.count() == 1


def test_late_payment_never_revives_hold(inventory):
    r, attempt = checkout(inventory)
    Reservation.objects.filter(id=r.id).update(
        created_at=timezone.now() - timedelta(minutes=10), expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert process_payment_event(payload(attempt))["status"] == "UNFULFILLED"
    assert not Booking.objects.exists()
    r.refresh_from_db()
    assert r.status == "EXPIRED"


def test_old_expiration_job_does_not_release_replacement(inventory):
    r = hold(inventory)
    Reservation.objects.filter(id=r.id).update(
        created_at=timezone.now() - timedelta(minutes=10), expires_at=timezone.now() - timedelta(seconds=1)
    )
    body, code = reserve_seat(inventory[0], inventory[2].id, "replacement")
    assert code == 201
    assert not expire_reservation(r.id)
    assert Reservation.objects.get(id=body["id"]).status == "HELD"


def test_expiration_commits_on_rejected_checkout(inventory):
    r = hold(inventory)
    Reservation.objects.filter(id=r.id).update(
        created_at=timezone.now() - timedelta(minutes=10), expires_at=timezone.now() - timedelta(seconds=1)
    )
    _, code = start_checkout(inventory[0], r.id, "expired")
    assert code == 409
    r.refresh_from_db()
    assert r.status == "EXPIRED"


def test_cancel_payment_race(inventory):
    r, attempt = checkout(inventory)
    run_concurrently(
        [lambda: cancel_reservation(inventory[0], r.id), lambda: process_payment_event(payload(attempt))]
    )
    r.refresh_from_db()
    assert r.status in ["CANCELLED", "CONFIRMED"]
    assert Booking.objects.filter(reservation=r).count() == (1 if r.status == "CONFIRMED" else 0)


def test_expire_payment_race(inventory):
    r, attempt = checkout(inventory)
    Reservation.objects.filter(id=r.id).update(
        created_at=timezone.now() - timedelta(minutes=10), expires_at=timezone.now() - timedelta(seconds=1)
    )
    run_concurrently([lambda: expire_reservation(r.id), lambda: process_payment_event(payload(attempt))])
    r.refresh_from_db()
    assert r.status == "EXPIRED"
    assert not Booking.objects.exists()


def test_failure_then_success_is_terminal(inventory):
    r, attempt = checkout(inventory)
    assert process_payment_event(payload(attempt, outcome="failure"))["status"] == "FAILED"
    assert process_payment_event(payload(attempt, event_id="late-success"))["status"] == "FAILED"
    assert not Booking.objects.exists()


def test_rollback_removes_outbox_and_hold(inventory):
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            hold(inventory)
            raise RuntimeError("simulated database rollback")
    assert not Reservation.objects.exists()
    assert not OutboxEvent.objects.exists()


def test_different_seat_not_blocked(inventory):
    user, event, seat = inventory
    other = EventSeat.objects.create(event=event, label="A02", price=100)
    with transaction.atomic():
        EventSeat.objects.select_for_update().get(id=seat.id)
        with ThreadPoolExecutor(max_workers=1) as pool:

            def reserve_other():
                try:
                    return reserve_seat(user, other.id, "independent")[1]
                finally:
                    connections.close_all()

            assert pool.submit(reserve_other).result(timeout=4) == 201
