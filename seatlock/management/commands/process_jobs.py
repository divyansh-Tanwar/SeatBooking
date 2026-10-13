"""Optional local recovery runner; production uses Celery and Redis."""

import time
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from seatlock.tasks import expire_holds, dispatch_outbox


class Command(BaseCommand):
    help = "Process durable jobs without a broker (development/recovery only)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise RuntimeError("Use Celery workers in production.")
        while True:
            close_old_connections()
            expire_holds()
            dispatch_outbox()
            if options["once"]:
                return
            time.sleep(2)
