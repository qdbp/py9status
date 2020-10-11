import time
from bisect import bisect
from glob import glob
from typing import Tuple

from py9status.core import BLUE, BROWN, ORANGE, PY9Unit, color
from py9status.default_units import DSA


class PY9Disk(PY9Unit):
    """
    Monitors disk activity.
    """

    BARS = (" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")
    THRESHS = (1.0,) + tuple(1 << i for i in range(10, 24, 2))

    def __init__(self, disk, *args, **kwargs) -> None:
        """
        Args:
            disk:
                the disk label as found in `/dev/`, e.g. "sda", etc.
        """
        super().__init__(*args, **kwargs)

        self.disk = disk
        self.stat_fn = f"/sys/class/block/{disk}/stat"
        self._ss = self._get_sector_size()
        self.last_r, self.last_w, self.last_t = self._read_rw()

    def _read_rw(self) -> Tuple[int, int, float]:
        """
        Returns the number of bytes read and written since last call, as
        well as the time of read.
        """
        with open(self.stat_fn, "r") as f:
            spl = f.read().split()
            return self._ss * int(spl[2]), self._ss * int(spl[6]), time.time()

    def _get_sector_size(self) -> int:
        candidates = glob("/sys/block/*")

        best = None
        best_len = -1

        for cand in candidates:
            base = cand.split("/")[-1]
            if self.disk.startswith(base) and len(base) > best_len:
                best = base
                best_len = len(base)

        if best is None:
            self._fail = f"no disk {self.disk}"
            return 0

        self._fail = False

        with open("/sys/block/" + best + "/queue/hw_sector_size") as f:
            return int(f.read())

    async def read(self) -> DSA:
        """

        Returns: dict:
            "err_no_disk": (bool, "The given disk or parttion was not found."),
            "bps_read": (float, "Bytes per second read from disk"),
            "bps_write": (float, "Bytes per second written to disk"),

        """

        # if we've faled before, try to recover
        if self._fail:
            self._ss = self._get_sector_size()

        try:
            if self._ss == 0:
                raise ValueError
            r, w, t = self._read_rw()
        except (OSError, ValueError):
            return {"err_no_disk": True}

        dr = r - self.last_r
        dw = w - self.last_w
        dt = t - self.last_t
        self.last_r, self.last_w, self.last_t = r, w, t

        return {"bps_read": dr / dt, "bps_write": dw / dt}

    def format(self, info: DSA) -> str:

        context = "disk [" + self.disk + " {}]"

        if info.pop("err_no_disk", False):
            return context.format(color("absent", BROWN))

        r_bar = self.BARS[bisect(self.THRESHS, info["bps_read"])]
        w_bar = self.BARS[bisect(self.THRESHS, info["bps_write"])]

        return context.format(color(r_bar, BLUE) + color(w_bar, ORANGE))
