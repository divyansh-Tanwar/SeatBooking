import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from django.core.asgi import get_asgi_application

django_application = get_asgi_application()
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.urls import path
from seatlock.consumers import SeatConsumer

application = ProtocolTypeRouter(
    {
        "http": django_application,
        "websocket": AllowedHostsOriginValidator(
            URLRouter(
                [
                    path("ws/events/<uuid:event_id>/", SeatConsumer.as_asgi()),
                ]
            )
        ),
    }
)
