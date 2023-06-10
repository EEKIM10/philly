import nio
import logging


# noinspection PyTypeChecker
class DebugCallbacks:
    def __init__(self, client: nio.AsyncClient):
        self.client = client
        self.log = logging.getLogger(__name__)

    def mount(self):
        self.log.debug("Adding on_sync callback (debugging)")
        self.client.add_event_callback(self.on_sync, nio.SyncResponse)
        self.log.debug("Adding on_sync_error callback (debugging)")
        self.client.add_event_callback(self.on_sync_error, nio.SyncError)
        self.log.debug("Adding on_unknown_event (debugging)")
        self.client.add_event_callback(self.on_unknown_event, nio.UnknownEvent)
        self.log.debug("Adding on_any_event (debugging)")
        self.client.add_event_callback(self.on_any_event, nio.Event)

    async def on_any_event(self, *args):
        """Called on any event"""
        self.log.debug("Event received: %r", args)

    async def on_sync(self, response: nio.SyncResponse):
        """Called when a sync loop successfully completes."""
        if self.client.should_upload_keys:
            self.log.info("Key upload response: %r", await self.client.keys_upload())
        self.log.debug("Sync loop completed. Response: %r", response)

    async def on_sync_error(self, exception: nio.SyncError):
        """Called when a sync loop fails"""
        self.log.error("Sync loop failed. Exception: %r", exception)

    async def on_unknown_event(self, event: nio.UnknownEvent, *extra):
        """Called when the client receives an event that is not known."""
        self.log.warning("Unknown event received: %r (Extra: %r)", event, extra)
