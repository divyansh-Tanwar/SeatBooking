from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Event


class SeatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        event_id = self.scope["url_route"]["kwargs"]["event_id"]
        exists = await database_sync_to_async(Event.objects.filter(id=event_id, published=True).exists)()
        if not exists:
            await self.close(code=4404)
            return
        self.group = "event." + str(event_id)
        try:
            await self.channel_layer.group_add(self.group, self.channel_name)
        except Exception:
            await self.close(code=1013)
            return
        await self.accept()
        await self.send_json({"type": "subscribed", "event_id": str(event_id)})

    async def disconnect(self, close_code):
        if hasattr(self, "group"):
            try:
                await self.channel_layer.group_discard(self.group, self.channel_name)
            except Exception:
                pass

    async def seat_changed(self, event):
        await self.send_json(
            {"type": "seat.changed", "seat_id": event["seat_id"], "version": event["version"]}
        )
