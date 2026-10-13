from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import hashlib
import hmac
import json
import pytest
from django.conf import settings
from django.db import connections
from rest_framework.test import APIClient
from rest_framework.exceptions import AuthenticationFailed
from seatlock.authentication import create_session, rotate
from seatlock.models import User, AuthSession, Event, PaymentAttempt
from seatlock.services import reserve_seat, start_checkout

pytestmark = pytest.mark.django_db(transaction=True)


def test_refresh_rotation_revokes_on_replay(inventory):
    access, raw = create_session(inventory[0])
    _, replacement = rotate(raw)
    assert replacement != raw
    with pytest.raises(AuthenticationFailed):
        rotate(raw)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + access)
    assert client.get("/api/auth/me").status_code == 401
    assert AuthSession.objects.get().revoked


def test_concurrent_refresh_single_use(inventory):
    _, raw = create_session(inventory[0])
    barrier = Barrier(2)

    def attempt():
        try:
            barrier.wait(timeout=10)
            try:
                rotate(raw)
                return "ok"
            except AuthenticationFailed:
                return "replay"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["ok", "replay"]
    assert AuthSession.objects.get().revoked


def test_ownership_on_all_reservation_routes(inventory):
    body, _ = reserve_seat(inventory[0], inventory[2].id, "hold")
    other = User.objects.create_user(username="mallory")
    client = APIClient()
    client.force_authenticate(other)
    path = "/api/reservations/" + body["id"]
    assert client.get(path).status_code == 404
    assert client.post(path + "/cancel").status_code == 404
    assert client.post(path + "/confirm", {}, format="json", HTTP_IDEMPOTENCY_KEY="x").status_code == 404
    assert client.get("/api/bookings").json()["count"] == 0


def test_register_cannot_choose_role():
    client = APIClient(enforce_csrf_checks=True)
    csrf = client.get("/api/auth/csrf").json()["csrfToken"]
    data = {"username": "newperson", "password": "A-great-password-888!", "role": "ORGANIZER"}
    assert client.post("/api/auth/register", data, format="json").status_code == 403
    response = client.post("/api/auth/register", data, format="json", HTTP_X_CSRFTOKEN=csrf)
    assert response.status_code == 201
    assert User.objects.get(username="newperson").role == "CUSTOMER"
    assert response.cookies["refresh"]["httponly"]


def test_logout_revokes_access(inventory):
    access, raw = create_session(inventory[0])
    client = APIClient(enforce_csrf_checks=True)
    csrf = client.get("/api/auth/csrf").json()["csrfToken"]
    client.cookies["refresh"] = raw
    assert client.post("/api/auth/logout", HTTP_X_CSRFTOKEN=csrf).status_code == 204
    client.credentials(HTTP_AUTHORIZATION="Bearer " + access)
    assert client.get("/api/auth/me").status_code == 401


def test_organizer_ownership_and_published_immutability(inventory):
    client = APIClient()
    client.force_authenticate(inventory[0])
    assert client.get("/api/organizer/events").status_code == 403
    stranger = User.objects.create_user(username="otherhost", role="ORGANIZER")
    client.force_authenticate(stranger)
    assert client.post(f"/api/organizer/events/{inventory[1].id}/publish").status_code == 404
    client.force_authenticate(inventory[1].organizer)
    response = client.post(
        f"/api/organizer/events/{inventory[1].id}/seats",
        {"rows": 2, "columns": 3, "price": 100},
        format="json",
    )
    assert response.status_code == 409


def test_public_snapshot_has_no_customer_identifiers(inventory):
    reserve_seat(inventory[0], inventory[2].id, "hold")
    client = APIClient()
    response = client.get(f"/api/events/{inventory[1].id}/seats")
    assert response.status_code == 200
    seat = response.json()["seats"][0]
    assert seat["status"] == "HELD"
    assert set(seat) == {"id", "label", "price", "version", "status"}


def test_payment_callback_requires_signature(inventory):
    body, _ = reserve_seat(inventory[0], inventory[2].id, "hold")
    start_checkout(inventory[0], body["id"], "pay")
    attempt = PaymentAttempt.objects.get()
    data = json.dumps(
        {
            "attempt_id": str(attempt.id),
            "event_id": "signed",
            "amount": attempt.amount,
            "currency": attempt.currency,
            "outcome": "success",
        }
    )
    client = APIClient()
    assert client.post("/api/payments/callback", data, content_type="application/json").status_code == 403
    signature = hmac.new(settings.SIMULATOR_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
    assert (
        client.post(
            "/api/payments/callback",
            data,
            content_type="application/json",
            HTTP_X_SIMULATOR_SIGNATURE=signature,
        ).status_code
        == 200
    )


def test_pagination(inventory):
    original = inventory[1]
    Event.objects.bulk_create(
        [
            Event(
                organizer=original.organizer,
                title=f"Event {i}",
                venue="Hall",
                starts_at=original.starts_at,
                booking_deadline=original.booking_deadline,
                published=True,
            )
            for i in range(25)
        ]
    )
    data = APIClient().get("/api/events").json()
    assert data["count"] == 26 and len(data["results"]) == 20 and data["next"]


def test_home_and_static_settings(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Save your seat" in response.content
    assert "csrftoken" in response.cookies
