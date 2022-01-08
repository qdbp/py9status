import asyncio as aio
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from py9status.core import PY9Unit, color


class PY9IP(PY9Unit):

    executor = ThreadPoolExecutor(max_workers=1)

    def _read_ipecho(self) -> dict[str, Any]:
        try:
            return {
                "ip": urllib.request.urlopen("http://ipecho.net/plain")
                .read()
                .decode("ascii")
            }
        except Exception:
            return {"err_failed_read": True}

    async def read(self) -> dict[str, Any]:
        """
        Returns: dict:
            "ip": str, "publicly-visible ip as returned by ipecho.net"
            "err_failed_read", bool, True if we failed to read our ip.
        """

        return await aio.get_event_loop().run_in_executor(
            self.executor, self._read_ipecho
        )

    def format(self, read_output: dict[str, Any]) -> str:
        if "err_failed_read" in read_output:
            return color("failed to read ip", "red")
        else:
            return read_output["ip"]
