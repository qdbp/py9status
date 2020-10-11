from datetime import datetime as dtt
from multiprocessing import cpu_count
from typing import Tuple

from py9status.core import PY9Unit, colorize_float, format_duration
from py9status.default_units import DSA


class PY9Time(PY9Unit):
    """
    Displays the current time.
    """

    def __init__(
        self, *args, fmt: str = "%a %b %d %Y - %H:%M", **kwargs
    ) -> None:

        """
        Args:
            fmt:
                the format for strftime to print the date in. Defaults
                to something sensible.
        """
        self.fmt = fmt
        self.uptime_fn = "/proc/uptime"
        self.loadavg_fn = "/proc/loadavg"

        self._doing_uptime = False

        _cpu_count = cpu_count()
        self._load_color_scale = tuple(
            _cpu_count * x for x in [0.1, 0.25, 0.50, 0.75]
        )

        super().__init__(*args, **kwargs)

    def _read_uptime(self) -> float:
        with open(self.uptime_fn) as uf:
            return float(uf.read().split(" ")[0])

    def _read_loadavg(self) -> Tuple[float, float, float]:
        with open(self.loadavg_fn) as lf:
            # noinspection PyTypeChecker
            return tuple(map(float, lf.read().split(" ")[:3]))

    async def read(self) -> DSA:
        """
        Returns:
            dict:
                "datestr" (float): current formatted datetime
                "uptime" (int): Current uptime, in seconds
                "loadavg_1/5/10" (float): One/five/ten minute load average
        """

        l1, l5, l10 = self._read_loadavg()

        return {
            "datestr": dtt.now().strftime(self.fmt),
            "uptime": self._read_uptime(),
            "loadavg_1": l1,
            "loadavg_5": l5,
            "loadavg_10": l10,
        }

    def format(self, output: DSA):
        if not self._doing_uptime:
            return output["datestr"]
        else:
            ut_s = format_duration(output["uptime"])
            lss = [
                colorize_float(
                    output[f"loadavg_{key}"], 3, 2, self._load_color_scale
                )
                for key in ["1", "5", "10"]
            ]

            return f"uptime [{ut_s}] load [{lss[0]}/{lss[1]}/{lss[2]}]"

    def handle_click(self, *args) -> None:
        self._doing_uptime = not self._doing_uptime
