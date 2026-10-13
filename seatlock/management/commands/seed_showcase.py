"""Public demo inventory without shared login credentials or mutable past events."""

from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from seatlock.models import Event, EventSeat, User


class Command(BaseCommand):
    help = "Create upcoming public-demo events. Does not create a shared customer login."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.HOSTED_DEMO:
            raise CommandError("This command requires HOSTED_DEMO=1.")
        owner, created = User.objects.get_or_create(
            username="seatlock-showcase-owner", defaults={"role": "ORGANIZER", "is_active": False}
        )
        if created:
            owner.set_unusable_password()
            owner.save(update_fields=["password"])
        if owner.is_active or owner.has_usable_password():
            raise CommandError("Showcase owner must be an inactive account without a usable password.")
        now = timezone.now()
        for title, venue, price, days in [
            ("Midnight Frequencies", "The Glasshouse · Bengaluru", 149900, 14),
            ("Ideas After Hours", "Studio Nine · Mumbai", 79900, 21),
            ("The Sunday Sessions", "Open Air Theatre · Pune", 99900, 28),
        ]:
            if Event.objects.filter(organizer=owner, title=title, booking_deadline__gt=now).exists():
                continue
            event = Event.objects.create(
                organizer=owner,
                title=title,
                venue=venue,
                published=True,
                description="Public portfolio demo. Simulated booking; no real tickets or payments.",
                starts_at=now + timedelta(days=days),
                booking_deadline=now + timedelta(days=days, hours=-1),
            )
            EventSeat.objects.bulk_create(
                [
                    EventSeat(event=event, label=f"{chr(65 + row)}{column:02}", price=price)
                    for row in range(6)
                    for column in range(1, 11)
                ]
            )
        self.stdout.write("Public showcase inventory is ready. Visitors create their own accounts.")
