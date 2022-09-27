import subprocess as sbp
from re import findall
from typing import Any

from py9status.core import RED, VIOLET, PY9Unit, color, get_color
from py9status.default_units import DSA


class PY9Wireless(PY9Unit):
    """
    Monitors wireless network information.
    """

    def __init__(self, wlan_if: str, *args, **kwargs) -> None:
        """
        Args:
            wlan_if: name of the wireless interface to monitor.
        """

        self.show_ssid = True
        self.wlan_if = wlan_if
        super().__init__(*args, requires=["iw"], **kwargs)

    async def read(self) -> DSA:
        """
        Returns: dict:
            "ssid": (str, "SSID of the connected network."),
            "quality": (float, "Connection quality, from 0 to 1."),
                This is defined as the fraction of the way from -80 dBm to
                -30 dBm.
            "err_down": (bool, "True if the wireless interface is down."),
            "err_disconnected": (
                bool,
                "True if there is no network connection.",

        """
        # Raw
        link = sbp.check_output(["iw", "dev", self.wlan_if, "link"]).decode(
            "ascii"
        )
        station = sbp.check_output(
            ["iw", "dev", self.wlan_if, "station", "dump"]
        ).decode("ascii")

        # Status
        # No device detected case
        if "No such device" in link:  # if not connected: 'No such device'
            return {"err_down": True}
        # Not connected case

        if not station.strip():  # if not connected: 'ESSID:off/any'
            return {"err_disconnected": True}

        # Raw output data
        raw_ssid = findall(r"SSID: ([^\n]+)", link)[0]
        power = float(findall(r"\n\s*signal:\s*(-\d+)", station)[0])

        return {
            "ssid": raw_ssid,
            "quality": min(1.0, max(0.0, (power + 80) / 50)),
        }

    def format(self, output: DSA) -> str:
        prefix = "wlan {} [".format(self.wlan_if)
        suffix = "]"

        if output.pop("err_down", False):
            return prefix + color("down", RED) + suffix

        elif output.pop("err_disconnected", False):
            return prefix + color("---", VIOLET) + suffix

        template = prefix + "{}] [{}%" + suffix
        quality = 100 * output["quality"]
        q_color = get_color(quality, do_reverse=True)
        q_str = color("{:3.0f}".format(quality), q_color)

        return template.format(
            output["ssid"] if self.show_ssid else "<>", q_str
        )

    def handle_click(self, click: dict[str, Any]) -> None:

        self.show_ssid ^= True
