from datetime import timedelta
from asgiref.sync import async_to_sync
import pytest
from channels.testing import WebsocketCommunicator
from django.utils import timezone
from seatlock.models import OutboxEvent, Booking, PaymentAttempt
from seatlock.services import reserve_seat, start_checkout
from seatlock.tasks import claim_event, deliver, dispatch_outbox

pytestmark = pytest.mark.django_db(transaction=True)


def test_worker_crash_after_payment_replays_safely(inventory):
    body, _ = reserve_seat(inventory[0], inventory[2].id, "hold")
    start_checkout(inventory[0], body["id"], "pay")
    row = OutboxEvent.objects.get(kind="payment")
    deliver(row)
    deliver(row)  # delivery succeeded but worker died before acknowledging the outbox record
    assert Booking.objects.count() == 1
    assert PaymentAttempt.objects.get().status == "SUCCEEDED"


def test_abandoned_outbox_lease_recovered(inventory):
    reserve_seat(inventory[0], inventory[2].id, "hold")
    first = claim_event()
    assert first is not None
    assert claim_event() is None
    OutboxEvent.objects.filter(id=first.id).update(lease_until=timezone.now() - timedelta(seconds=1))
    second = claim_event()
    assert second.id == first.id and second.lease_token != first.lease_token


def test_failed_delivery_retries_without_losing_hold(inventory, monkeypatch):
    reserve_seat(inventory[0], inventory[2].id, "hold")

    def fail(row):
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr("seatlock.tasks.deliver", fail)
    assert dispatch_outbox() == 0
    row = OutboxEvent.objects.get()
    assert row.delivered_at is None and row.attempts == 1 and row.lease_token is None
    assert row.available_at > timezone.now()


def test_websocket_subscribe_reconnect_and_public_message(inventory, settings):
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    from config.asgi import application
    from channels.layers import get_channel_layer

    async def exercise():
        for _ in range(2):
            socket = WebsocketCommunicator(
                application, f"/ws/events/{inventory[1].id}/", headers=[(b"origin", b"http://localhost")]
            )
            connected, _ = await socket.connect()
            assert connected
            assert (await socket.receive_json_from())["type"] == "subscribed"
            await get_channel_layer().group_send(
                "event." + str(inventory[1].id),
                {"type": "seat.changed", "seat_id": str(inventory[2].id), "version": 1},
            )
            data = await socket.receive_json_from()
            assert data == {"type": "seat.changed", "seat_id": str(inventory[2].id), "version": 1}
            await socket.disconnect()

    async_to_sync(exercise)()
