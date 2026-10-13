from datetime import timedelta
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from seatlock.models import User, Event, EventSeat


class Command(BaseCommand):
    help = "Create local demo accounts and events. Refuses to run with DEBUG=0."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise RuntimeError("Demo credentials are only allowed with DEBUG=1.")
        organizer, created = User.objects.get_or_create(
            username="organizer", defaults={"role": "ORGANIZER", "email": "organizer@example.test"}
        )
        if created:
            organizer.set_password("SeatLock-demo-2026!")
            organizer.save()
        user, created = User.objects.get_or_create(username="demo", defaults={"email": "demo@example.test"})
        if created:
            user.set_password("SeatLock-demo-2026!")
            user.save()
        for title, venue, days, price in [
            ("Midnight Frequencies", "The Glasshouse · Bengaluru", 14, 149900),
            ("Ideas After Hours", "Studio Nine · Mumbai", 21, 79900),
            ("The Sunday Sessions", "Open Air Theatre · Pune", 28, 99900),
        ]:
            if Event.objects.filter(title=title, organizer=organizer).exists():
                continue
            event = Event.objects.create(
                organizer=organizer,
                title=title,
                venue=venue,
                description="A carefully curated evening. Find your place and make it a night to remember.",
                starts_at=timezone.now() + timedelta(days=days),
                booking_deadline=timezone.now() + timedelta(days=days, hours=-1),
                published=True,
            )
            EventSeat.objects.bulk_create(
                [
                    EventSeat(event=event, label=f"{chr(65 + r)}{c:02}", price=price)
                    for r in range(6)
                    for c in range(1, 11)
                ]
            )
        self.stdout.write(
            self.style.SUCCESS("Demo ready. Users: demo / organizer; password: SeatLock-demo-2026!")
        )
