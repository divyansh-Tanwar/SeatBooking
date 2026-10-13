from datetime import timedelta
import pytest
from django.utils import timezone
from seatlock.models import User, Event, EventSeat


@pytest.fixture
def inventory(db):
    customer = User.objects.create_user(
        username="alice", password="An-example-password-981!", email="alice@example.test"
    )
    organizer = User.objects.create_user(username="host", role="ORGANIZER")
    event = Event.objects.create(
        organizer=organizer,
        title="Test event",
        venue="Test hall",
        starts_at=timezone.now() + timedelta(days=2),
        booking_deadline=timezone.now() + timedelta(days=1),
        published=True,
    )
    seat = EventSeat.objects.create(event=event, label="A01", price=149900)
    return customer, event, seat
