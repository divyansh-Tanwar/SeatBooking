import logging
import uuid
from datetime import timedelta
from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import OutboxEvent, Reservation, Booking, PaymentAttempt
from .services import db_now, expire_reservation, process_payment_event

logger = logging.getLogger(__name__)


@shared_task
def expire_holds():
    ids = list(
        Reservation.objects.filter(status="HELD", expires_at__lte=timezone.now())
        .order_by("expires_at")
        .values_list("id", flat=True)[:200]
    )
    for reservation_id in ids:
        expire_reservation(reservation_id)
    return len(ids)


def claim_event():
    with transaction.atomic():
        now = db_now()
        row = (
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(delivered_at__isnull=True, available_at__lte=now)
            .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
            .order_by("available_at")
            .first()
        )
        if not row:
            return None
        row.lease_token = uuid.uuid4()
        row.lease_until = now + timedelta(seconds=90)
        row.attempts += 1
        row.save(update_fields=["lease_token", "lease_until", "attempts"])
        return row


def deliver(row):
    if row.kind == "seat":
        if settings.LOCAL_POLLING:
            return
        async_to_sync(get_channel_layer().group_send)(
            "event." + row.payload["event_id"], {"type": "seat.changed", **row.payload}
        )
    elif row.kind == "payment":
        attempt = PaymentAttempt.objects.get(id=row.payload["attempt_id"])
        process_payment_event(
            {
                "event_id": "sim:" + str(attempt.id),
                "attempt_id": str(attempt.id),
                "amount": attempt.amount,
                "currency": attempt.currency,
                "outcome": "failure" if attempt.scenario == "failure" else "success",
            }
        )
    elif row.kind == "email":
        booking = Booking.objects.select_related("reservation__customer", "reservation__seat__event").get(
            id=row.payload["booking_id"]
        )
        r = booking.reservation
        if r.customer.email:
            send_mail(
                "Your SeatLock booking is confirmed",
                f"Booking {booking.id}\n{r.seat.event.title}\nSeat {r.seat.label}\nSee you there!",
                None,
                [r.customer.email],
            )
    else:
        raise ValueError("Unknown outbox kind")


@shared_task
def dispatch_outbox():
    count = 0
    for _ in range(50):
        row = claim_event()
        if not row:
            break
        try:
            deliver(row)
        except Exception as exc:
            logger.exception("Outbox delivery failed: %s", row.id)
            OutboxEvent.objects.filter(id=row.id, lease_token=row.lease_token).update(
                lease_until=None,
                lease_token=None,
                available_at=timezone.now() + timedelta(seconds=min(300, 2 ** min(row.attempts, 8))),
                last_error=type(exc).__name__,
            )
        else:
            OutboxEvent.objects.filter(id=row.id, lease_token=row.lease_token).update(
                delivered_at=timezone.now(), lease_until=None, lease_token=None, last_error=""
            )
            count += 1
    return count
