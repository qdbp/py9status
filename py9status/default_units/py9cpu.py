import time
from atexit import register as atexit_register
from collections import deque
from glob import glob
from statistics import mean
from typing import Deque as dq_t, Optional, Tuple

from py9status.core import ORANGE, PY9Unit, color, get_color, mk_tcolor_str
from py9status.default_units import DSA


class PY9CPU(PY9Unit):
    """
    Monitors CPU usage and temperature.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.stat_file = open("/proc/stat", "r")
        atexit_register(self.stat_file.close)

        self.tt0, self.tu0, self.tk0 = self._read_cpu_times()
        self.show_breakdown = False

        # to make sure that cpuinfo is updated at least once on read
        time.sleep(0.01)

        self._us: dq_t[float] = deque([], maxlen=int(2 / self.poll_interval))
        self._ks: dq_t[float] = deque([], maxlen=int(2 / self.poll_interval))
        self._temps: dq_t[int] = deque([], maxlen=int(2 / self.poll_interval))

    def _read_cpu_times(self) -> Tuple[int, int, int]:
        self.stat_file.seek(0)
        comps = [int(x) for x in self.stat_file.readline().split(" ")[1:] if x]
        return sum(comps), comps[0] + comps[1], comps[2]

    async def read(self) -> DSA:
        """

        Returns: dict:
            "p_u": (float, "Fraction of cpu time used by userland."),
            "p_k": (float, "Fraction of cpu time used by kernel."),
            "temp_C": (float, "Average cpu temperature."),
            "err_no_temp": (bool, "Temperature could not be read."),

        """
        tt, tu, tk = self._read_cpu_times()
        dtt = tt - self.tt0
        dtu = tu - self.tu0
        dtk = tk - self.tk0
        self.tt0, self.tu0, self.tk0 = tt, tu, tk

        self._us.append(dtu / dtt)
        self._ks.append(dtk / dtt)

        out = {"p_k": mean(self._ks), "p_u": mean(self._us)}

        temp: Optional[float] = 0.0
        n_cores = 0

        for fn in glob("/sys/class/thermal/thermal_zone*/temp"):
            with open(fn, "r") as f:
                try:
                    temp += float(f.read()) / 1000
                    n_cores += 1
                except ValueError:
                    temp = None  # type: ignore

        if n_cores > 0 and temp is not None:
            temp /= n_cores
            self._temps.append(temp)
            out["temp_C"] = mean(self._temps)
        else:
            self._temps.clear()
            out["err_no_temp"] = True

        return out

    def format(self, output: DSA) -> str:
        pu = output["p_u"] * 100
        pk = output["p_k"] * 100

        no_temp = output.pop("err_no_temp", False)

        if no_temp:
            color_str = color("unk", ORANGE)
        else:
            temp = output["temp_C"]
            color_str = mk_tcolor_str(temp)

        if self.show_breakdown:
            load_str = (
                "u "
                + color(f"{pu:3.0f}", get_color(pu))
                + "% k"
                + color(f"{pk:3.0f}", get_color(pk))
                + "%"
            )
        else:
            load_str = "load " + color(f"{pu + pk:3.0f}%", get_color(pu + pk))

        return "cpu [" + load_str + "] [temp " + color_str + "C]"

    def handle_click(self, *args) -> None:
        self.show_breakdown ^= True
