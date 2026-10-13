from django.core.management.base import BaseCommand
from seatlock.models import PaymentAttempt
from seatlock.services import process_payment_event


class Command(BaseCommand):
    help = "Inject a deterministic payment result for recovery demonstrations."

    def add_arguments(self, parser):
        parser.add_argument("attempt_id")
        parser.add_argument("--outcome", choices=["success", "failure"], default="success")
        parser.add_argument("--event-id")

    def handle(self, *args, **options):
        attempt = PaymentAttempt.objects.get(id=options["attempt_id"])
        self.stdout.write(
            str(
                process_payment_event(
                    {
                        "attempt_id": str(attempt.id),
                        "event_id": options["event_id"] or "manual:" + str(attempt.id),
                        "amount": attempt.amount,
                        "currency": attempt.currency,
                        "outcome": options["outcome"],
                    }
                )
            )
        )
