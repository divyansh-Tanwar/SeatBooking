import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q, F


class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER"
        ORGANIZER = "ORGANIZER"

    role = models.CharField(max_length=12, choices=Role.choices, default=Role.CUSTOMER)


class AuthSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    expires_at = models.DateTimeField()
    revoked = models.BooleanField(default=False)


class RefreshCredential(models.Model):
    session = models.ForeignKey(AuthSession, on_delete=models.CASCADE)
    digest = models.CharField(max_length=64, unique=True)
    consumed = models.BooleanField(default=False)
    expires_at = models.DateTimeField()


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organizer = models.ForeignKey(User, on_delete=models.PROTECT)
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True, max_length=4000)
    venue = models.CharField(max_length=150)
    starts_at = models.DateTimeField()
    booking_deadline = models.DateTimeField()
    currency = models.CharField(max_length=3, default="INR")
    published = models.BooleanField(default=False)

    class Meta:
        ordering = ["starts_at", "id"]
        indexes = [models.Index(fields=["published", "starts_at"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(booking_deadline__lte=F("starts_at")), name="deadline_before_event"
            )
        ]


class EventSeat(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="seats")
    label = models.CharField(max_length=12)
    price = models.PositiveIntegerField()
    version = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["label"]
        constraints = [models.UniqueConstraint(fields=["event", "label"], name="unique_event_seat")]


class Reservation(models.Model):
    class Status(models.TextChoices):
        HELD = "HELD"
        CONFIRMED = "CONFIRMED"
        EXPIRED = "EXPIRED"
        CANCELLED = "CANCELLED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(User, on_delete=models.PROTECT)
    seat = models.ForeignKey(EventSeat, on_delete=models.PROTECT, related_name="reservations")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.HELD)
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    price = models.PositiveIntegerField()
    currency = models.CharField(max_length=3)

    class Meta:
        indexes = [
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["customer", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["seat"],
                condition=Q(status__in=["HELD", "CONFIRMED"]),
                name="one_active_reservation_per_seat",
            ),
            models.CheckConstraint(
                condition=Q(expires_at__gt=F("created_at")), name="positive_hold_duration"
            ),
            models.CheckConstraint(
                condition=Q(status__in=["HELD", "CONFIRMED", "EXPIRED", "CANCELLED"]),
                name="valid_reservation_status",
            ),
        ]


class Booking(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reservation = models.OneToOneField(Reservation, on_delete=models.PROTECT, related_name="booking")
    confirmed_at = models.DateTimeField()


class PaymentAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reservation = models.OneToOneField(Reservation, on_delete=models.PROTECT, related_name="payment")
    amount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=24, default="PENDING")
    scenario = models.CharField(max_length=12, default="success")


class PaymentEvent(models.Model):
    event_id = models.CharField(max_length=100, unique=True)
    attempt = models.ForeignKey(PaymentAttempt, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64)
    result = models.CharField(max_length=24)


class IdempotencyRecord(models.Model):
    customer = models.ForeignKey(User, on_delete=models.CASCADE)
    operation = models.CharField(max_length=100)
    key = models.CharField(max_length=100)
    fingerprint = models.CharField(max_length=64)
    response = models.JSONField(default=dict)
    status_code = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["customer", "operation", "key"], name="unique_request_key")
        ]


class OutboxEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    available_at = models.DateTimeField()
    lease_until = models.DateTimeField(null=True)
    lease_token = models.UUIDField(null=True)
    delivered_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        indexes = [models.Index(fields=["delivered_at", "available_at"])]
