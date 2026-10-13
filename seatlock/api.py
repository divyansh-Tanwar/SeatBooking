import hashlib
import hmac
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection, transaction, OperationalError
from django.http import JsonResponse
from django.middleware.csrf import get_token, CsrfViewMiddleware
from django.shortcuts import render
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, authentication_classes, throttle_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import exception_handler as drf_exception_handler
from .authentication import create_session, rotate, digest
from .models import User, AuthSession, RefreshCredential, Event, EventSeat, Reservation, Booking, OutboxEvent
from .services import (
    DomainError,
    reserve_seat,
    cancel_reservation,
    start_checkout,
    reservation_data,
    process_payment_event,
    db_now,
)


def exception_handler(exc, context):
    from rest_framework.exceptions import AuthenticationFailed

    if isinstance(exc, AuthenticationFailed):
        return Response(
            {"error": {"code": "authentication_failed", "message": str(exc)}},
            status=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    if isinstance(exc, DomainError):
        return Response({"error": {"code": exc.code, "message": exc.message}}, status=exc.status)
    if isinstance(exc, OperationalError):
        cause = getattr(exc, "__cause__", None)
        if getattr(cause, "sqlstate", None) in ("55P03", "57014", "40P01", "40001"):
            return Response(
                {"error": {"code": "retry_later", "message": "Busy; retry with the same key."}},
                status=503,
                headers={"Retry-After": "1"},
            )
    response = drf_exception_handler(exc, context)
    if response is not None:
        response.data = {"error": {"code": "request_rejected", "message": response.data}}
    return response


class AuthThrottle(AnonRateThrottle):
    scope = "auth"


def csrf_check(request):
    raw = request._request
    check = CsrfViewMiddleware(lambda r: None)
    check.process_request(raw)
    # Deliberately check against a non-exempt view: DRF APIViews are otherwise csrf_exempt.
    rejected = check.process_view(raw, lambda r: None, (), {})
    if rejected:
        raise PermissionDenied("CSRF check failed.")


def home(request):
    get_token(request)
    return render(
        request,
        "index.html",
        {
            "local_polling": settings.LOCAL_POLLING,
            "show_demo_login": settings.DEBUG,
        },
    )


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def csrf(request):
    return Response({"csrfToken": get_token(request)})


class Credentials(serializers.Serializer):
    username = serializers.RegexField(r"^[\w.@+-]+$", max_length=150)
    password = serializers.CharField(max_length=128, trim_whitespace=False)
    email = serializers.EmailField(required=False)


def auth_response(user, tokens, status=200):
    access, refresh = tokens
    response = Response(
        {"access": access, "user": {"username": user.username, "role": user.role}}, status=status
    )
    response.set_cookie(
        "refresh",
        refresh,
        max_age=7 * 86400,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="Strict",
        path="/api/auth/",
    )
    response["Cache-Control"] = "no-store"
    return response


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([AuthThrottle])
def register(request):
    csrf_check(request)
    serializer = Credentials(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if User.objects.filter(username=data["username"]).exists():
        raise ValidationError({"username": "Username is already taken."})
    user = User(username=data["username"], email=data.get("email", ""))
    try:
        validate_password(data["password"], user)
    except DjangoValidationError as exc:
        raise ValidationError({"password": exc.messages})
    user.set_password(data["password"])
    from django.db import IntegrityError

    try:
        with transaction.atomic():
            user.save()
    except IntegrityError:
        raise ValidationError({"username": "Username is already taken."})
    return auth_response(user, create_session(user), 201)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([AuthThrottle])
def login(request):
    csrf_check(request)
    serializer = Credentials(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = authenticate(
        username=serializer.validated_data["username"], password=serializer.validated_data["password"]
    )
    if user is None:
        from rest_framework.exceptions import AuthenticationFailed

        raise AuthenticationFailed("Invalid credentials.")
    return auth_response(user, create_session(user))


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([AuthThrottle])
def refresh(request):
    csrf_check(request)
    raw = request.COOKIES.get("refresh", "")
    tokens = rotate(raw)
    credential = RefreshCredential.objects.select_related("session__user").get(digest=digest(tokens[1]))
    return auth_response(credential.session.user, tokens)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def logout(request):
    csrf_check(request)
    credential = RefreshCredential.objects.filter(digest=digest(request.COOKIES.get("refresh", ""))).first()
    if credential:
        with transaction.atomic():
            session = AuthSession.objects.select_for_update().get(id=credential.session_id)
            session.revoked = True
            session.save(update_fields=["revoked"])
    response = Response(status=204)
    response.delete_cookie("refresh", path="/api/auth/", samesite="Strict")
    return response


@api_view(["GET"])
def me(request):
    return Response({"username": request.user.username, "role": request.user.role})


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "venue",
            "starts_at",
            "booking_deadline",
            "currency",
            "published",
        ]
        read_only_fields = ["id", "published"]

    def validate(self, data):
        if data["booking_deadline"] <= timezone.now() or data["booking_deadline"] > data["starts_at"]:
            raise serializers.ValidationError("Deadline must be in the future and no later than event start.")
        if data.get("currency", "INR") not in ["INR", "USD", "EUR", "GBP"]:
            raise serializers.ValidationError("Supported currencies: INR, USD, EUR, GBP.")
        return data


def paginated(request, queryset, convert):
    paginator = PageNumberPagination()
    page = paginator.paginate_queryset(queryset, request)
    return paginator.get_paginated_response([convert(obj) for obj in page])


@api_view(["GET"])
@permission_classes([AllowAny])
def events(request):
    queryset = Event.objects.filter(published=True)
    if request.query_params.get("search"):
        queryset = queryset.filter(title__icontains=request.query_params["search"][:120])
    return paginated(request, queryset, lambda event: EventSerializer(event).data)


@api_view(["GET", "POST"])
def organizer_events(request):
    if request.user.role != "ORGANIZER":
        raise PermissionDenied("Organizer role required.")
    if request.method == "GET":
        return paginated(
            request, Event.objects.filter(organizer=request.user), lambda event: EventSerializer(event).data
        )
    serializer = EventSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    event = serializer.save(organizer=request.user)
    return Response(EventSerializer(event).data, status=201)


class SeatInput(serializers.Serializer):
    rows = serializers.IntegerField(min_value=1, max_value=26)
    columns = serializers.IntegerField(min_value=1, max_value=30)
    price = serializers.IntegerField(min_value=0, max_value=100000000)


@api_view(["POST"])
def create_seats(request, event_id):
    if request.user.role != "ORGANIZER":
        raise PermissionDenied("Organizer role required.")
    serializer = SeatInput(data=request.data)
    serializer.is_valid(raise_exception=True)
    with transaction.atomic():
        event = Event.objects.select_for_update().filter(id=event_id, organizer=request.user).first()
        if not event:
            raise DomainError("not_found", "Event not found.", 404)
        if event.published or event.seats.exists():
            raise DomainError("immutable_inventory", "Inventory already exists or the event is published.")
        data = serializer.validated_data
        EventSeat.objects.bulk_create(
            [
                EventSeat(event=event, label=f"{chr(65 + r)}{c:02}", price=data["price"])
                for r in range(data["rows"])
                for c in range(1, data["columns"] + 1)
            ]
        )
    return Response({"created": data["rows"] * data["columns"]}, status=201)


@api_view(["POST"])
def publish(request, event_id):
    if request.user.role != "ORGANIZER":
        raise PermissionDenied("Organizer role required.")
    with transaction.atomic():
        event = Event.objects.select_for_update().filter(id=event_id, organizer=request.user).first()
        if not event:
            raise DomainError("not_found", "Event not found.", 404)
        if not event.seats.exists() or event.booking_deadline <= timezone.now():
            raise DomainError("invalid_event", "Add seats and a future booking deadline first.")
        event.published = True
        event.save(update_fields=["published"])
    return Response(EventSerializer(event).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def seats(request, event_id):
    event = Event.objects.filter(id=event_id, published=True).first()
    if not event:
        raise DomainError("not_found", "Event not found.", 404)
    # Single SQL query: correlated subqueries keep availability and seat version in one statement snapshot.
    from django.db.models import OuterRef, Subquery

    active = Reservation.objects.filter(seat_id=OuterRef("pk"), status__in=["HELD", "CONFIRMED"])
    rows = EventSeat.objects.filter(event=event).annotate(
        allocation=Subquery(active.values("status")[:1]), deadline=Subquery(active.values("expires_at")[:1])
    )
    rows = list(rows)
    now = db_now()
    result = []
    for seat in rows:
        state = seat.allocation or "AVAILABLE"
        if state == "HELD" and seat.deadline <= now:
            state = "AVAILABLE"
        result.append(
            {
                "id": str(seat.id),
                "label": seat.label,
                "price": seat.price,
                "version": seat.version,
                "status": state,
            }
        )
    response = Response(
        {"event": EventSerializer(event).data, "server_time": now.isoformat(), "seats": result}
    )
    response["Cache-Control"] = "no-store"
    return response


class ReserveInput(serializers.Serializer):
    seat_id = serializers.UUIDField()


@api_view(["POST"])
def reserve(request):
    data = ReserveInput(data=request.data)
    data.is_valid(raise_exception=True)
    body, status = reserve_seat(
        request.user, data.validated_data["seat_id"], request.headers.get("Idempotency-Key")
    )
    return Response(body, status=status)


@api_view(["GET"])
def reservation(request, reservation_id):
    r = Reservation.objects.filter(id=reservation_id, customer=request.user).first()
    if not r:
        raise DomainError("not_found", "Reservation not found.", 404)
    # A GET reports effective expiration without mutating inventory.
    data = reservation_data(r)
    if r.status == "HELD" and r.expires_at <= db_now():
        data["status"] = "EXPIRED"
    data["server_time"] = db_now().isoformat()
    return Response(data, headers={"Cache-Control": "no-store"})


@api_view(["POST"])
def cancel(request, reservation_id):
    body, status = cancel_reservation(request.user, reservation_id)
    return Response(body, status=status)


@api_view(["POST"])
def confirm(request, reservation_id):
    if request.data:
        raise ValidationError("Confirmation does not accept payment outcomes or prices.")
    body, status = start_checkout(request.user, reservation_id, request.headers.get("Idempotency-Key"))
    return Response(body, status=status)


@api_view(["GET"])
def bookings(request):
    query = (
        Booking.objects.filter(reservation__customer=request.user)
        .select_related("reservation__seat__event")
        .order_by("-confirmed_at", "id")
    )
    return paginated(
        request,
        query,
        lambda b: {
            "id": str(b.id),
            "confirmed_at": b.confirmed_at.isoformat(),
            "reservation": reservation_data(b.reservation),
        },
    )


class PaymentInput(serializers.Serializer):
    event_id = serializers.CharField(max_length=100)
    attempt_id = serializers.UUIDField()
    amount = serializers.IntegerField(min_value=0)
    currency = serializers.ChoiceField(choices=["INR", "USD", "EUR", "GBP"])
    outcome = serializers.ChoiceField(choices=["success", "failure"])


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def payment_callback(request):
    expected = hmac.new(settings.SIMULATOR_SECRET.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("X-Simulator-Signature", "")):
        raise PermissionDenied("Invalid simulator signature.")
    serializer = PaymentInput(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = dict(serializer.validated_data)
    payload["attempt_id"] = str(payload["attempt_id"])
    return Response(process_payment_event(payload))


def live(request):
    return JsonResponse({"status": "alive"})


def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "not_ready"}, status=503)
    return JsonResponse({"status": "ready"})


@api_view(["GET"])
def operations(request):
    if request.user.role != "ORGANIZER":
        raise PermissionDenied("Organizer role required.")
    import redis

    try:
        redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1).ping()
        broker = "available"
    except Exception:
        broker = "unavailable"
    pending = OutboxEvent.objects.filter(delivered_at__isnull=True)
    return Response(
        {
            "redis": broker,
            "outbox_pending": pending.count(),
            "outbox_overdue": pending.filter(available_at__lt=timezone.now() - timedelta(minutes=2)).count(),
        }
    )
