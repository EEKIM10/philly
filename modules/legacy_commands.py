import logging

import nio
import asyncio
import time
import typing

if typing.TYPE_CHECKING:
    from main import Client


class LegacyCommands:
    def __init__(self, client: "Client"):
        self.client = client
        self.to_mount = {
            "ping": self.ping,
            "echo": self.echo,
            "join": self.join,
            "leave": self.leave,
            "trust": self.trust,
            "edit": self.edit_test,
        }
        self.log = logging.getLogger(__name__)

    def mount(self):
        # noinspection PyTypeChecker
        self.client.add_event_callback(self.is_command, nio.RoomMessageText)

    async def is_command(self, room: nio.MatrixRoom, event: nio.RoomMessageText):
        if self.client.start_time > event.server_timestamp / 1000:
            delta = (self.client.start_time - event.server_timestamp / 1000)
            self.log.warning("Ignoring message from %d seconds ago, sent before bot started.", delta)
            return

        try:
            command, args = self.client.parse_command_args(event.body)
        except ValueError:
            await self.client.update_receipt_marker(room.room_id, event.event_id)
            return  # not a command
        else:
            if command.lower() in self.to_mount:
                asyncio.create_task(self.client.update_receipt_marker(room.room_id, event.event_id))
                try:
                    await self.to_mount[command.lower()](room, event, args)
                except Exception as e:
                    self.log.error("Error in command %r: %s", command, e, exc_info=e)

    async def edit_test(self, room: nio.MatrixRoom, event: nio.RoomMessageText, _):
        message = await self.client.reply_to(room, event, "This message will be edited in 5 seconds.")
        await asyncio.sleep(5)
        body = {
            "msgtype": "m.text",
            "body": "This message has been edited.",
            "m.new_content": {
                "msgtype": "m.text",
                "body": "This message has been edited.",
            },
            "m.relates_to": {
                "rel_type": "m.replace",
                "event_id": message.event_id,
            }
        }
        await self.client.room_send(room.room_id, "m.room.message", body)
    
    async def ping(self, room: nio.MatrixRoom, event: nio.RoomMessageText, _):
        """Shows latency"""
        server_timestamp = event.server_timestamp
        now = round(time.time_ns() / 1000000)
        latency = now - server_timestamp
        await self.client.reply_to(
            room,
            event,
            "Pong! Approximately {:,}ms RTT (Server TS was {!s}, local TS was {!s})".format(
                latency, server_timestamp, now
            )
        )

    async def echo(self, room: nio.MatrixRoom, event: nio.RoomMessageText, args: str):
        """Echoes the given text"""
        args = args.replace("@", "@\u200b").replace("\\", "\\\u200b")
        await self.client.reply_to(room, event, args)

    async def join(self, room: nio.MatrixRoom, event: nio.RoomMessageText, args: str):
        """Joins the given room"""
        if self.client.owner != event.sender:
            return await self.client.reply_to(room, event, "You are not my owner!")
        if not args:
            await self.client.reply_to(room, event, "Usage: join <room>")
            return
        await self.client.join(args)
        await self.client.reply_to(room, event, "Joined room %r" % args)

    async def leave(self, room: nio.MatrixRoom, event: nio.RoomMessageText, args: str):
        """Leaves the given room"""
        if self.client.owner != event.sender:
            return await self.client.reply_to(room, event, "You are not my owner!")
        if not args:
            await self.client.reply_to(room, event, "Usage: leave <room>")
            return
        await self.client.room_forget(args)
        await self.client.reply_to(room, event, "Left room %r" % args)

    async def trust(self, room: nio.MatrixRoom, event: nio.RoomMessageText, args: str):
        """Trusts the given user"""
        if self.client.owner != event.sender:
            return await self.client.reply_to(room, event, "You are not my owner!")
        if not args:
            await self.client.reply_to(room, event, "Usage: trust <user>")
            return
        try:
            for device_id, olm_device in self.client.device_store[args[0]]:
                self.client.verify_device(olm_device)
        except KeyError:
            await self.client.reply_to(room, event, "User %r not found" % args)
        else:
            await self.client.reply_to(room, event, "Trusted user %r" % args)
