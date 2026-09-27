"""Serve a simulated installation on a TCP port."""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Self

from .gateway import Gateway
from .topology import Installation, load_file, preset

_LOGGER = logging.getLogger(__name__)


class NikobusSimulator:
    """A TCP endpoint a host connects to as it would to a bridge."""

    def __init__(self, installation: Installation, host: str = "127.0.0.1", port: int = 0) -> None:
        self.installation = installation
        self.gateway = Gateway(installation)
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        """The port actually bound (useful when asking for port 0)."""
        if self._server is None:
            return self._port
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def connection_string(self) -> str:
        return f"{self._host}:{self.port}"

    async def start(self) -> Self:
        self._server = await asyncio.start_server(
            self._serve_client, self._host, self._port
        )
        _LOGGER.info("Nikobus simulator listening on %s", self.connection_string)
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> Self:
        return await self.start()

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

    async def _serve_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        _LOGGER.info("client connected: %s", peer)
        buffer = ""
        try:
            while data := await reader.read(256):
                buffer += data.decode("ascii", errors="ignore").replace("\n", "\r")
                while "\r" in buffer:
                    line, buffer = buffer.split("\r", 1)
                    for answer in self.gateway.handle(line):
                        writer.write((answer + "\r").encode("ascii"))
                    await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            _LOGGER.info("client gone: %s", peer)
            writer.close()


def main() -> None:
    """Run a simulated installation from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--preset", help="one of the installations shipped here")
    source.add_argument("--file", help="a declaration in JSON or YAML")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    installation = preset(args.preset) if args.preset else load_file(args.file)
    simulator = NikobusSimulator(installation, args.host, args.port)

    async def run() -> None:
        async with simulator:
            print(f"Point the integration at {simulator.connection_string}")
            await asyncio.Event().wait()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
