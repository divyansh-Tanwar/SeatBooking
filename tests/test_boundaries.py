from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import time
from threading import Event as ThreadEvent
import pytest
from django.db import connections, transaction
from django.utils import timezone
from rest_framework.test import APIClient
from seatlock.models import Reservation, EventSeat, PaymentAttempt, Booking
from seatlock.services import reserve_seat, start_checkout, process_payment_event

pytestmark = pytest.mark.django_db(transaction=True)


def test_waiting_checkout_rechecks_deadline(inventory):
    user, _, seat = inventory
    body, _ = reserve_seat(user, seat.id, "hold")
    r = Reservation.objects.get(id=body["id"])
    Reservation.objects.filter(id=r.id).update(expires_at=timezone.now() + timedelta(seconds=1))
    entered = ThreadEvent()

    def checkout():
        try:
            entered.set()
            return start_checkout(user, r.id, "pay")
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            EventSeat.objects.select_for_update().get(id=seat.id)
            future = pool.submit(checkout)
            assert entered.wait(timeout=5)
            time.sleep(1.2)
        assert future.result(timeout=10)[1] == 409
    assert not PaymentAttempt.objects.exists()


def test_old_callback_cannot_take_replacement(inventory):
    user, _, seat = inventory
    body, _ = reserve_seat(user, seat.id, "hold")
    start_checkout(user, body["id"], "pay")
    attempt = PaymentAttempt.objects.get()
    Reservation.objects.filter(id=body["id"]).update(
        created_at=timezone.now() - timedelta(minutes=10), expires_at=timezone.now() - timedelta(seconds=1)
    )
    replacement, code = reserve_seat(user, seat.id, "new-hold")
    assert code == 201
    result = process_payment_event(
        {
            "event_id": "late",
            "attempt_id": str(attempt.id),
            "amount": attempt.amount,
            "currency": attempt.currency,
            "outcome": "success",
        }
    )
    assert result["status"] == "UNFULFILLED"
    assert Reservation.objects.get(id=replacement["id"]).status == "HELD"
    assert not Booking.objects.exists()


def test_invalid_credentials_return_401():
    client = APIClient()
    csrf = client.get("/api/auth/csrf").json()["csrfToken"]
    response = client.post(
        "/api/auth/login",
        {"username": "missing", "password": "Not-real-882!"},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 401
