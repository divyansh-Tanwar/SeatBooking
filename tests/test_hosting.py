import pytest
from django.core.management import call_command
from config.hosting import database_from_url
from seatlock.models import Event, User


def test_hosted_database_url():
    config = database_from_url(
        "postgresql://alice:p%40ss@db.example:5432/seatlock?sslmode=require&channel_binding=require"
    )
    assert config["PASSWORD"] == "p@ss"
    assert config["HOST"] == "db.example"
    assert config["OPTIONS"]["sslmode"] == "require"
    assert config["DISABLE_SERVER_SIDE_CURSORS"]


def test_invalid_database_url():
    with pytest.raises(ValueError):
        database_from_url("https://example.com")


@pytest.mark.django_db
def test_public_seed_has_no_shared_credentials(settings):
    settings.HOSTED_DEMO = True
    settings.DEBUG = False
    call_command("seed_showcase")
    call_command("seed_showcase")
    assert Event.objects.count() == 3
    owner = User.objects.get(username="seatlock-showcase-owner")
    assert not owner.is_active and not owner.has_usable_password()
    assert not User.objects.filter(username="demo").exists()
    assert all(event.seats.count() == 60 for event in Event.objects.all())


@pytest.mark.django_db
def test_public_home_hides_local_password(client, settings):
    settings.DEBUG = False
    response = client.get("/")
    assert b"SeatLock-demo-2026!" not in response.content
