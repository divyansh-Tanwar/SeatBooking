"""Optional local recovery runner; production uses Celery and Redis."""

import time
import logging
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from seatlock.tasks import expire_holds, dispatch_outbox


class Command(BaseCommand):
    help = "Process durable jobs without a broker (development or explicit hosted demo)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if not (settings.DEBUG or settings.HOSTED_DEMO):
            raise RuntimeError("Use Celery workers in production.")
        while True:
            close_old_connections()
            try:
                expire_holds()
                dispatch_outbox()
            except Exception:
                if options["once"]:
                    raise
                logging.getLogger(__name__).exception("Background cycle failed; retrying")
                time.sleep(5)
            if options["once"]:
                return
            time.sleep(2)
