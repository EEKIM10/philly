import datetime
import json
import time
import typing
from typing import Tuple, Dict, Any

import nio
import re
import asyncio
import logging
import click
import yaml
from utils import validate_config, Database
from modules.debug import DebugCallbacks
from modules.legacy_commands import LegacyCommands
from modules.ytdl import YoutubeDownloadModule


# async def command(room: nio.MatrixRoom, event: nio.RoomMessageText, args: str) -> Optional[Any:
COMMAND_TYPE = typing.Coroutine[
    typing.Tuple[nio.MatrixRoom, nio.RoomMessageText, str], typing.Optional[Any], None
]


class Client(nio.AsyncClient):
    def __init__(self, config: dict):
        self._original_config = config.copy()
        self._config, missing = validate_config(config)
        if missing:
            raise ValueError(f"Missing required config keys: {' '.join(missing)}")
        super().__init__(
            homeserver=config["homeserver"],
            user=self._config.get("user", ""),
            device_id=self._config.get("logging", {}).get("device_name"),
            store_path=self._config.get("store_path", "./state"),
        )
        # self.db = Database()
        # self.loop.run_until_complete(
        #     self.db.connect(self._config["database"])
        # )
        self.log = logging.getLogger("client")
        # noinspection PyTypeChecker
        self.add_event_callback(self.on_invite, nio.InviteMemberEvent)
        self.loop = None
        self.start_time = None
        self.owner = self._config.get("owner", "@nex:nexy7574.co.uk")

        self.registered_commands: Dict[str, Dict[str, COMMAND_TYPE]] = {}
        self.initial_sync = asyncio.Event()
        self.join_lock = asyncio.Lock()
        # { module: { command_name: callback } }

        DebugCallbacks(self).mount()
        LegacyCommands(self).mount()
        YoutubeDownloadModule(self).mount()

    async def listen_for(self, callback, event):
        """Wraps a callback to avoid fatal errors when calling callbacks."""
        async def wrapped(*args,):
            try:
                await callback(*args)
            except Exception as e:
                self.log.exception("Error in callback: %r", e)
        self.add_event_callback(wrapped, event)

    def _list_command_names(self):
        """Lists all commands that are registered."""
        return [x for y in self.registered_commands.values() for x in y.keys()]

    @staticmethod
    def _parse_command_name(name: str):
        name = name.lower()
        if "." in name:
            raise NameError("Command names cannot contain periods, as those are reserved for internal references.")

        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            raise NameError("Command names can only contain lowercase letters, numbers, underscores, and dashes.")

        reserved = [
            "commands",
            "command",
            "module"
        ]

        return name

    def register_command(self, callback: COMMAND_TYPE, module: str = None, command: str = None):
        """
        Registers a command for the built-in callback handler to listen for.

        This will add *three* commands (pretend your prefix is !):

        - `!command`
        - `!module.command`
        - `!module.commands.function_name`

        Command names cannot contain:

        - Periods (`.`), as those are reserved for internal references (last two examples above)
        - Uppercase letters (These are converted to lowercase)
        - Spaces
        - Symbols (except for underscores and dashes)

        Command names do not have to be unique, but come on a last-one-wins basis.
        If you have two commands with the same name, only the last one to be mounted will be called.

        Commands will fail quietly (an error level log is pushed, but there is no feedback to the command itself),
        so you should make sure you handle errors appropriately.
        """
        if module is None:
            # Get the name of the class it is contained by, or if none, the name of the module it is contained in
            qualified = callback.__qualname__
            if "." in qualified:
                module = '.'.join(qualified.split(".")[:-1])
            else:
                module = callback.__module__
        if command is None:
            command = callback.__name__

        command = command.lower()
        # name p

        self.log.debug("Registering command: %s.%s", module, command)
        self.registered_commands.setdefault(module, {})

    async def start(self):
        """Starts the bot."""
        self.log.info("Starting bot.")
        self.loop = asyncio.get_running_loop()
        login_kw = self._config.get("login", {})
        if login_kw:
            if all((login_kw.get(x) for x in ("token", "device_name"))):
                self.log.debug("Logging in with stored token - no actual login request to display.")
                self.access_token = login_kw["token"]
                self.device_id = login_kw["device_name"]
                self.user_id = self._config["user"]
            elif login_kw.get("password"):
                self.log.info("Logging in with password")
                login_response = await self.login(**login_kw)
                self.log.debug("Login response: %r", login_response)
                if not login_response:
                    raise ValueError("Login failed.")
                elif isinstance(login_response, nio.LoginError):
                    raise ValueError(f"Login failed: {login_response.message}")
                else:
                    self.log.info("Login successful.")
            else:
                self.log.critical("No login information provided. Please provide a login dict in your config.")
                raise RuntimeError("No login information provided. Please provide a login dict in your config.")

        else:
            raise ValueError("No login information provided. Please provide a login dict in your config.")

        self.start_time = time.time()
        self.log.info("Performing first sync manually")
        result = await self.sync(timeout=1000, full_state=True, set_presence="unavailable")
        self.log.info("Completed first sync")
        self.log.debug("First sync result: %r", result)
        if isinstance(result, nio.SyncError):
            if result.status_code == "M_UNKNOWN_TOKEN":
                self.log.critical("Sync failed - invalid token.")
            return

        # noinspection PyUnresolvedReferences
        # for room in result.rooms.invite:
        #     self.log.info(f"Invited to room: {room!r}")
        #     asyncio.create_task(self.join(room))

        self.log.info("Starting sync loop.")
        try:
            self.initial_sync.set()
            await self.sync_forever(
                timeout=30_000,
                full_state=True,
                set_presence="online",
            )
        except (KeyboardInterrupt, EOFError):
            if self._config.get("cleanup", True):
                self.log.info("Logging out (removes session & token).")
                await self.logout()
            self.log.info("Closing HTTP session")
            await self.close()
    
    async def room_send(self, room_id: str, message_type: str, content: dict, **kwargs):
        """Sends a message to a room."""
        kwargs: typing.Dict[str, Any] = {
            "room_id": room_id,
            "message_type": message_type,
            "content": content,
            **kwargs
        }
        kwargs.setdefault("ignore_unverified_devices", True)
        return await super().room_send(**kwargs)

    async def sync(self, *args, **kwargs):
        self.log.debug("Starting sync request")
        result = await super().sync(*args, **kwargs)
        self.log.debug("Sync request completed")
        if self.should_upload_keys:
            self.log.info("Key upload response: %r", await self.keys_upload())
        self.log.debug("Sync response: %r", result)
        return result

    async def reply_to(self, room: nio.MatrixRoom, message: nio.RoomMessageText, text: str):
        """Helper to reply to a message."""
        await self.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": text,
                "m.relates_to": {
                    "m.in_reply_to": {
                        "event_id": message.event_id
                    }
                }
            },
            ignore_unverified_devices=True
        )

    @staticmethod
    def parse_command_args(value: str, *, strip_prefix: bool = True) -> Tuple[str, str]:
        """Parses a command and its arguments"""
        try:
            command, args = value.split(" ", 1)
        except ValueError:
            command = value
            args = ""
        if strip_prefix:
            if command[0] != "!":
                raise ValueError("Message does not start with the prefix")
            else:
                command = command[1:]
        return command, args

    async def on_invite(self, room: nio.MatrixRoom, event: nio.InviteMemberEvent):
        """Called when invited to a room."""
        async def do_join():
            HELLO = "<p>Hello, I'm a Phillip, the Matrix port of " \
                    "<a href=\"https://discord.gg/3uTzPRdU4W\">the discord Jimmy" \
                    "</a>.</p>\n" \
                    "<p>I'm still in development, so please be patient with me.</p>\n" \
                    "<p>Note that encrypted rooms are not currently supported, and as such functionality " \
                    "will be very patchy.</p>\n<p>Prefix: <code>!</code></p>\n\n" \
                    "<p><i>Invited by: %s</i></p>"
            async with self.join_lock:
                self.log.info(f"Invited to {room.display_name} by {event.sender}")
                if not self.initial_sync.is_set():
                    self.log.info("Waiting until first sync is complete before processing invite.")
                    await self.initial_sync.wait()
                if room.room_id not in self.rooms.keys():
                    self.log.info(f"Joining {room.display_name}")
                    try:
                        result = await self.join(room.room_id)
                        if isinstance(result, nio.JoinResponse):
                            await asyncio.sleep(1)
                            await self.room_send(
                                room_id=room.room_id,
                                message_type="m.room.message",
                                content={
                                    "msgtype": "m.text",
                                    "body": HELLO % event.sender,
                                    "format": "org.matrix.custom.html",
                                    "formatted_body": HELLO % event.sender,
                                }
                            )
                        else:
                            self.log.error(f"Failed to join room: {result}")
                    except nio.exceptions.LocalProtocolError as e:
                        self.log.error(f"Failed to join room: {e}")
                        return
                else:
                    self.log.info(f"Already in {room.display_name}. Discarding event.")

        asyncio.create_task(do_join())


@click.group()
@click.pass_context
def cli_root(ctx: click.Context):
    """Matrix jimmy bot test"""
    try:
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config = {}

    ctx.obj = {
        "config": config,
    }
    # Redirect logs to a file if specified
    if ctx.obj["config"].get("logging"):
        log_meta = ctx.obj["config"]["logging"]
        log_level = log_meta.get("level", "INFO")
        log_level = getattr(logging, log_level.upper(), logging.DEBUG)
        log_file = log_meta.get("file")

        kwargs = {
            "level": log_level,
            "format": "[%(asctime)s/%(levelname)s @ %(name)s] %(message)s",
            "datefmt": "%d/%m/%Y %H:%M:%S"
        }
        if log_file:
            kwargs["filename"] = log_file
            mode = log_meta.get("mode", "a")[0].lower()
            if mode not in ("a", "w"):
                raise ValueError(f"Invalid log mode: {mode}. Must be one of (A)ppend, or over(W)rite.")
            kwargs["filemode"] = mode
        logging.basicConfig(**kwargs)
        if log_meta.get("stdout_mirror", False) is True:
            # Mirror output log to stdout
            stdout_handler = logging.StreamHandler()
            stdout_handler.setLevel(log_level)
            stdout_handler.setFormatter(logging.Formatter("[%(asctime)s/%(levelname)s @ %(name)s] %(message)s"))
            logging.getLogger().addHandler(stdout_handler)
        logging.info("Logging started.")


@cli_root.command(name="run")
@click.pass_context
def start_bot(ctx: click.Context):
    """Starts the bot."""
    async def runner():
        logging.debug("Initialising client")
        ctx.obj["client"] = Client(ctx.obj["config"])
        logging.debug("Starting client")
        try:
            await ctx.obj["client"].start()
        finally:
            logging.debug("Client exited, dumping config.")
            # Dump any configuration changes
            # noinspection PyProtectedMember
            if ctx.obj["client"]._config != ctx.obj["client"]._original_config:
                with open("config.yaml", "w") as f:
                    logging.info("Config was modified at runtime, dumping to file.")
                    # noinspection PyProtectedMember
                    yaml.safe_dump(ctx.obj["client"]._config, f)

            # noinspection PyProtectedMember
            if ctx.obj["client"]._config.get("cleanup", True):
                # Log out of the client, removing the session
                ctx.obj["client"].log.info("Logging out (removes session & token).")
                await ctx.obj["client"].logout()
            ctx.obj["client"].log.info("Closing HTTP session")
            await ctx.obj["client"].close()

    asyncio.run(runner())


@cli_root.command(name="get-access-token")
@click.option("--store", default=None, help="The directory to create a store in")
@click.argument("home_server", default=None)
@click.argument("username", default=None)
@click.argument("device_id", default=None)
@click.argument("user_id", default=None)
@click.pass_context
def get_access_token(
        ctx: click.Context,
        store: str | None,
        home_server: str | None,
        username: str | None,
        device_id: str | None,
        user_id: str | None
):
    """(helper) Get access token for the bot, so you don't have to put your password in your config."""
    from urllib.parse import urlparse
    import requests
    # Not that putting the access token in is much better
    home_server = home_server or click.prompt("Homeserver URL", default="https://matrix.org")
    hs_parsed = urlparse(home_server, scheme="http")
    logging.debug("Parsed domain stuff: %r", hs_parsed)
    click.secho("Contacting home server...", fg="cyan", nl=False)
    try:
        # noinspection PyProtectedMember
        loc = hs_parsed._replace(path="/.well-known/matrix/client")
        logging.debug("Resolving homeserver delegation (GET %s)", hs_parsed.geturl())
        response = requests.get(
            loc.geturl(),
            headers={
                "Accept": "application/json",
                "User-Agent": "CLI-Client (NioTestBot)"
            }
        )
    except requests.RequestException as e:
        logging.critical("Failed to resolve homeserver delegation:", exc_info=e)
        click.secho("Failed (Unknown error while contacting homeserver)", fg="red")
        return

    if response.status_code != 200:
        click.secho(f"Failed (HTTP {response.status_code})", fg="red")
        return
    try:
        content = response.json()
        logging.debug(content)
    except (ValueError, json.JSONDecodeError):
        click.secho(f"Failed, assuming {hs_parsed.netloc} is the homeserver.", fg="yellow")
    else:
        if "m.homeserver" in content:
            home_server = content["m.homeserver"]["base_url"]
            hs_parsed = urlparse(home_server)
            click.secho(f"Resolved homeserver to {home_server}", fg="green")
        else:
            click.secho(f"N/A (No delegation specified), assuming {hs_parsed.netloc} is the homeserver.", fg="yellow")

    domain = hs_parsed.netloc

    username = username or click.prompt("Username")
    password = click.prompt("Password", hide_input=True)
    click.echo("\b" * (len(password) + 1) + "*" * len(password))
    device_id = device_id or click.prompt("Device ID", default="jimmy-bot")

    user_id = user_id or f"@{username}:{domain}"
    if not click.confirm(f"Login as {user_id}? ", default=True):
        user_id = click.prompt("Fully qualified user ID")

    async def grabber():
        client = nio.AsyncClient(
            homeserver=home_server,
            user=user_id,
            device_id=device_id,
            store_path=store
        )
        click.secho(f"Logging in as {user_id}.", fg="cyan")
        click.confirm(
            "Your access token will appear in your console in plain text. Please confirm you want to continue",
            abort=True
        )

        login_response = await client.login(password)
        if isinstance(login_response, nio.LoginError):
            click.secho(f"Failed to login: {login_response.message!r}", fg="red")
            click.secho("Failed to get access token.", fg="red")
            await client.close()
            return None, None

        click.secho("Logged in successfully, syncing...", fg="green")
        await client.sync(timeout=30000, full_state=True)

        _token = client.access_token
        click.secho(f"Access token: {client.access_token}", fg="yellow")
        click.echo(f"Device ID: {client.device_id}")
        click.echo(f"Home server: {client.homeserver}")
        click.echo(f"User ID: {client.user_id}")
        await client.close()
        click.secho("Closed session.", fg="green")
        return client, _token

    r_client, token = asyncio.run(grabber())

    if r_client and click.confirm("Write to config.yaml? (will remove password) ", prompt_suffix=""):
        ctx.obj["config"]["login"] = {
            "token": token,
            "device_name": r_client.device_id,
        }
        with open("config.yaml", "w") as f:
            yaml.safe_dump(ctx.obj["config"], f)
        click.secho("Wrote to config.yaml.", fg="green")


if __name__ == "__main__":
    cli_root()
