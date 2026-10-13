from django.urls import path
from seatlock import api

urlpatterns = [
    path("", api.home),
    path("health/live", api.live),
    path("health/ready", api.ready),
    path("api/auth/csrf", api.csrf),
    path("api/auth/register", api.register),
    path("api/auth/login", api.login),
    path("api/auth/refresh", api.refresh),
    path("api/auth/logout", api.logout),
    path("api/auth/me", api.me),
    path("api/events", api.events),
    path("api/events/<uuid:event_id>/seats", api.seats),
    path("api/organizer/events", api.organizer_events),
    path("api/organizer/events/<uuid:event_id>/seats", api.create_seats),
    path("api/organizer/events/<uuid:event_id>/publish", api.publish),
    path("api/organizer/operations", api.operations),
    path("api/reservations", api.reserve),
    path("api/reservations/<uuid:reservation_id>", api.reservation),
    path("api/reservations/<uuid:reservation_id>/cancel", api.cancel),
    path("api/reservations/<uuid:reservation_id>/confirm", api.confirm),
    path("api/bookings", api.bookings),
    path("api/payments/callback", api.payment_callback),
]
