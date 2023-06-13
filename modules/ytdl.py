import json
import pathlib
import asyncio
import subprocess
from functools import partial

import nio
import aiofiles
import logging
import magic
from yt_dlp import YoutubeDL
import tempfile
import typing
if typing.TYPE_CHECKING:
    from main import Client

YTDL_ARGS: typing.Dict[str, typing.Any] = {
    "outtmpl": "%(title).50s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "no_warnings": True,
    "quiet": True,
    'noprogress': True,
    "nooverwrites": True,
    'format': "(bv+ba/b)[filesize<100M]/b"
}


class YoutubeDownloadModule:
    def __init__(self, client: "Client"):
        self.client = client
        self.to_mount = {
            "ytdl": self.ytdl,
        }
        self.log = logging.getLogger(__name__)

    def mount(self):
        # noinspection PyTypeChecker
        self.client.add_event_callback(self.is_command, nio.RoomMessageText)
        # self.client.register_command(self.ytdl, "ytdl", "yt-dl")

    async def is_command(self, room: nio.MatrixRoom, event: nio.RoomMessageText):
        if self.client.start_time > event.server_timestamp / 1000:
            delta = (self.client.start_time - event.server_timestamp / 1000)
            self.log.warning("Ignoring message from %d seconds ago, sent before bot started.", delta)
            return
        try:
            command, args = self.client.parse_command_args(event.body)
        except ValueError:
            return  # not a command
        else:
            if command.lower() in self.to_mount:
                try:
                    await self.to_mount[command.lower()](room, event, args)
                except Exception as e:
                    self.log.error("Error in command %r: %s", command, e, exc_info=e)

    def _download(self, url: str, download_format: str, *, temp_dir: str) -> typing.List[pathlib.Path]:
        args = YTDL_ARGS.copy()
        args["paths"] = {
            "temp": temp_dir,
            "home": temp_dir
        }
        if download_format:
            args["format"] = download_format
        else:
            args["format"] = "(bv+ba/b)[filesize<100M]"

        with YoutubeDL(args) as ytdl_instance:
            self.log.info("Downloading %s with format: %r", url, args["format"])
            ytdl_instance.download(
                [url]
            )

        return list(pathlib.Path(temp_dir).glob("*"))

    def get_metadata(self, file: pathlib.Path):
        _meta = subprocess.run(
            [
                "ffprobe",
                "-of",
                "json",
                "-loglevel",
                "9",
                "-show_entries",
                "stream=width,height",
                str(file)
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace"
        )
        if _meta.returncode != 0:
            self.log.warning("ffprobe failed (%d): %s", _meta.returncode, _meta.stderr)
            return
        return json.loads(_meta.stdout)

    async def upload_files(self, file: pathlib.Path):
        stat = file.stat()
        # max 99Mb
        if stat.st_size > 99 * 1024 * 1024:
            self.log.warning("File %s is too big (%d bytes)", file, stat.st_size)
            return
        mime = magic.Magic(mime=True).from_file(file)
        self.log.debug("File %s is %s", file, mime)
        metadata = self.get_metadata(file) or {}
        if not metadata.get("streams"):
            self.log.warning("No streams for %s", file)
            return
        if not metadata["streams"][0].get("width"):
            self.log.warning("No width for %s", file)
            return
        if not metadata["streams"][0].get("height"):
            self.log.warning("No height for %s", file)
            return

        body = {
            "body": file.name,
            "info": {
                "mimetype": mime,
                "h": int(metadata["streams"][0]["height"]),
                "w": int(metadata["streams"][0]["width"]),
                "size": stat.st_size,
            },
            "msgtype": "m." + mime.split("/")[0],
        }
        async with aiofiles.open(file, "r+b") as _file:
            size_mb = stat.st_size / 1024 / 1024
            self.log.info("Uploading %s (%dMb)", file, size_mb)
            response, keys = await self.client.upload(
                _file,
                content_type=mime,
                filename=file.name,
                filesize=stat.st_size
            )
            self.log.info("Uploaded %s", file)
            self.log.debug("%r (%r)", response, keys)
        if isinstance(response, nio.UploadResponse):
            body["url"] = response.content_uri
            return body

    async def get_video_info(self, url: str) -> dict:
        """Extracts JSON information about the video"""
        args = YTDL_ARGS.copy()
        with YoutubeDL(args) as ytdl_instance:
            info = ytdl_instance.extract_info(url, download=False)
        self.log.debug("ytdl info for %s: %r", url, info)
        return info

    async def ytdl(self, room: nio.MatrixRoom, event: nio.RoomMessageText, args: str):
        """Downloads a video from YouTube"""
        if not args:
            await self.client.reply_to(room, event, "Usage: !ytdl <url> [format]")
            return

        args = args.split()
        url = args.pop(0)
        dl_format = "(bv+ba/b)[filesize<100M]/b"
        if args:
            dl_format = args.pop(0)

        msg = await self.client.reply_to(room, event, "Downloading...")
        msg_id = msg.event_id
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                info = await self.get_video_info(url)
                if not info:
                    await self.client.edit_message(room, msg_id, "Could not get video info (Restricted?)")
                    return
                await self.client.edit_message(
                    room,
                    msg_id,
                    "Downloading [%r](%s)..." % (info["title"], info["original_url"]),
                    extra={
                        "m.new_content": {
                            "msgtype": "m.notice",
                            "format": "org.matrix.custom.html",
                            "body": "Downloading Downloading [%r](%s)..." % (info["title"], info["original_url"]),
                            "formatted_body": "Downloading <a href=\"%s\">%s</a>..." % (
                                info["original_url"], info["title"]
                            ),
                        }
                    }
                )
                self.log.info("Downloading %s to %s", url, temp_dir)
                files = await self.client.loop.run_in_executor(
                    None,
                    partial(self._download, url, dl_format, temp_dir=temp_dir)
                )
                self.log.info("Downloaded %d files", len(files))
                if not files:
                    await self.client.edit_message(room, msg_id, "No files downloaded")
                    return
                sent = False
                for file in files:
                    data = self.get_metadata(file)
                    size_mb = file.stat().st_size / 1024 / 1024
                    resolution = "%dx%d" % (data["streams"][0]["width"], data["streams"][0]["height"])
                    await self.client.edit_message(
                        room, msg_id, "Uploading %s (%dMb, %s)..." % (file.name, size_mb, resolution)
                    )
                    body = await self.upload_files(file)
                    self.log.debug("Upload body: %s", body)
                    if body:
                        body["m.relates_to"] = {
                            "m.in_reply_to": {
                                "event_id": event.event_id
                            }
                        }
                        await self.client.room_send(
                            room.room_id,
                            "m.room.message",
                            body
                        )
                        sent = True
                if sent:
                    await self.client.edit_message(
                        room,
                        msg_id,
                        "Completed, downloaded [your video]({})".format("url"),
                        extra={
                            "m.new_content": {
                                "msgtype": "m.text",
                                "format": "org.matrix.custom.html",
                                "body": "Completed, downloaded <a href=\"{}\">your video</a>".format(url),
                                "formatted_body": "Completed, downloaded <a href=\"{}\">your video</a>".format(url)
                            }
                        }
                    )
                    await asyncio.sleep(10)
                    await self.client.room_redact(room.room_id, msg_id, reason="Command completed.")
        except Exception as e:
            self.log.error("Error: %s", e, exc_info=e)
            await self.client.reply_to(room, event, "Error: " + str(e))
            return
