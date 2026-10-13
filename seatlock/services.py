"""All inventory mutations use seat -> reservation -> payment lock order."""

import hashlib
import json
from datetime import timedelta
from django.db import connection, transaction
from .models import (
    EventSeat,
    Reservation,
    Booking,
    PaymentAttempt,
    PaymentEvent,
    IdempotencyRecord,
    OutboxEvent,
)


class DomainError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def db_now():
    if connection.vendor != "postgresql":
        raise RuntimeError("Booking correctness requires PostgreSQL.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def transaction_limits():
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("SET LOCAL statement_timeout = '10s'")


def emit(kind, payload, available_at=None):
    OutboxEvent.objects.create(kind=kind, payload=payload, available_at=available_at or db_now())


def seat_changed(seat):
    seat.version += 1
    seat.save(update_fields=["version"])
    emit("seat", {"event_id": str(seat.event_id), "seat_id": str(seat.id), "version": seat.version})


def expire_locked(reservation, seat, now):
    if reservation.status == "HELD" and reservation.expires_at <= now:
        reservation.status = "EXPIRED"
        reservation.save(update_fields=["status"])
        seat_changed(seat)
        return True
    return False


def reservation_data(r):
    payment = PaymentAttempt.objects.filter(reservation=r).first()
    booking = Booking.objects.filter(reservation=r).first()
    return {
        "id": str(r.id),
        "seat_id": str(r.seat_id),
        "seat_label": r.seat.label,
        "event_id": str(r.seat.event_id),
        "event_title": r.seat.event.title,
        "status": r.status,
        "expires_at": r.expires_at.isoformat(),
        "price": r.price,
        "currency": r.currency,
        "payment_status": payment.status if payment else None,
        "booking_id": str(booking.id) if booking else None,
    }


def idempotent(user, operation, key, payload, action):
    if not key or len(key) > 100 or not key.isascii():
        raise DomainError(
            "invalid_idempotency_key", "Provide an ASCII Idempotency-Key up to 100 characters.", 400
        )
    with transaction.atomic():
        transaction_limits()
        # get_or_create handles simultaneous insert via a savepoint and the unique constraint.
        record, _ = IdempotencyRecord.objects.get_or_create(
            customer=user, operation=operation, key=key, defaults={"fingerprint": fingerprint(payload)}
        )
        record = IdempotencyRecord.objects.select_for_update().get(pk=record.pk)
        if record.fingerprint != fingerprint(payload):
            raise DomainError("key_reused", "This key was already used for different inputs.")
        if record.status_code:
            return record.response, record.status_code
        # Expected failures are values, so expiration changes are committed with their response.
        response, status = action()
        record.response, record.status_code = response, status
        record.save(update_fields=["response", "status_code"])
        return response, status


def error(code, message, status=409):
    return {"error": {"code": code, "message": message}}, status


def reserve_seat(user, seat_id, key):
    def action():
        seat = (
            EventSeat.objects.select_for_update(of=("self",))
            .select_related("event")
            .filter(id=seat_id, event__published=True)
            .first()
        )
        if not seat:
            return error("not_found", "Seat not found.", 404)
        active = (
            Reservation.objects.select_for_update()
            .filter(seat=seat, status__in=["HELD", "CONFIRMED"])
            .first()
        )
        now = db_now()
        if active:
            expire_locked(active, seat, now)
        if seat.event.booking_deadline <= now:
            return error("sales_closed", "Booking for this event has closed.")
        if active and active.status in ("HELD", "CONFIRMED"):
            return error("seat_unavailable", "Someone else already holds or booked this seat.")
        r = Reservation.objects.create(
            customer=user,
            seat=seat,
            created_at=now,
            expires_at=min(now + timedelta(minutes=5), seat.event.booking_deadline),
            price=seat.price,
            currency=seat.event.currency,
        )
        seat_changed(seat)
        return reservation_data(r), 201

    return idempotent(user, "reserve", key, {"seat_id": str(seat_id)}, action)


def owned_locked(user, reservation_id):
    candidate = Reservation.objects.filter(id=reservation_id, customer=user).first()
    if not candidate:
        return None, None
    seat = EventSeat.objects.select_for_update().get(id=candidate.seat_id)
    r = Reservation.objects.select_for_update(of=("self",)).select_related("seat__event").get(id=candidate.id)
    return seat, r


def cancel_reservation(user, reservation_id):
    with transaction.atomic():
        transaction_limits()
        seat, r = owned_locked(user, reservation_id)
        if not r:
            return error("not_found", "Reservation not found.", 404)
        expire_locked(r, seat, db_now())
        if r.status == "CONFIRMED":
            return error("already_confirmed", "Confirmed bookings cannot be cancelled in this demo.")
        if r.status == "HELD":
            r.status = "CANCELLED"
            r.save(update_fields=["status"])
            seat_changed(seat)
        return reservation_data(r), 200


def start_checkout(user, reservation_id, key):
    def action():
        seat, r = owned_locked(user, reservation_id)
        if not r:
            return error("not_found", "Reservation not found.", 404)
        expire_locked(r, seat, db_now())
        if r.status == "CONFIRMED":
            return reservation_data(r), 200
        if r.status != "HELD":
            return error("hold_ended", "This hold has ended. Choose an available seat.")
        attempt, created = PaymentAttempt.objects.get_or_create(
            reservation=r, defaults={"amount": r.price, "currency": r.currency}
        )
        if created:
            emit("payment", {"attempt_id": str(attempt.id)}, db_now() + timedelta(seconds=2))
        return {
            "attempt_id": str(attempt.id),
            "status_url": "/api/reservations/" + str(r.id),
            "status": attempt.status,
        }, 202

    return idempotent(user, "confirm:" + str(reservation_id), key, {}, action)


def expire_reservation(reservation_id):
    candidate = Reservation.objects.filter(id=reservation_id).first()
    if not candidate:
        return False
    with transaction.atomic():
        transaction_limits()
        seat = EventSeat.objects.select_for_update().get(id=candidate.seat_id)
        r = Reservation.objects.select_for_update().get(id=reservation_id)
        return expire_locked(r, seat, db_now())


def process_payment_event(payload):
    candidate = PaymentAttempt.objects.select_related("reservation").filter(id=payload["attempt_id"]).first()
    if not candidate:
        raise DomainError("unknown_attempt", "Unknown payment attempt.", 404)
    with transaction.atomic():
        transaction_limits()
        seat = EventSeat.objects.select_for_update().get(id=candidate.reservation.seat_id)
        r = Reservation.objects.select_for_update().get(id=candidate.reservation_id)
        attempt = PaymentAttempt.objects.select_for_update().get(id=candidate.id)
        if payload["amount"] != attempt.amount or payload["currency"] != attempt.currency:
            raise DomainError("payment_mismatch", "Payment amount or currency does not match.", 400)
        digest = fingerprint(payload)
        # The unique event ID also serializes malicious reuse across different attempts.
        receipt, created = PaymentEvent.objects.get_or_create(
            event_id=payload["event_id"],
            defaults={"attempt": attempt, "fingerprint": digest, "result": "PROCESSING"},
        )
        if not created:
            if receipt.fingerprint != digest:
                raise DomainError("event_reused", "Payment event ID reused with different content.")
            return {"status": receipt.result}
        now = db_now()
        expire_locked(r, seat, now)
        if attempt.status != "PENDING":
            result = attempt.status
        elif payload["outcome"] == "failure":
            result = "FAILED"
        elif r.status != "HELD":
            result = "UNFULFILLED"
        else:
            result = "SUCCEEDED"
            r.status = "CONFIRMED"
            r.save(update_fields=["status"])
            booking = Booking.objects.create(reservation=r, confirmed_at=now)
            seat_changed(seat)
            emit("email", {"booking_id": str(booking.id)})
        attempt.status = result
        attempt.save(update_fields=["status"])
        receipt.result = result
        receipt.save(update_fields=["result"])
        return {"status": result}
