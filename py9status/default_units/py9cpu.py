import re
import time
from atexit import register as atexit_register
from collections import deque
from glob import glob
from shutil import which
from subprocess import check_output

from typing import Deque as dq_t, Tuple

from py9status.core import ORANGE, PY9Unit, RED, color, get_color, mk_tcolor_str
from py9status.default_units import DSA


class PY9CPU(PY9Unit):
    """
    Monitors CPU usage and temperature.
    """

    AMD_RYZEN_PAT1 = re.compile(r"Tccd1:\s+\+([0-9]{2}\.[0-9])", re.MULTILINE)
    AMD_RYZEN_PAT2 = re.compile(r"Tccd2:\s+\+([0-9]{2}\.[0-9])", re.MULTILINE)

    @staticmethod
    def get_cpuinfo():
        with open("/proc/cpuinfo") as f:
            raw = f.read()

        return re.findall(r"^model name:\s+(.*)$", raw, re.MULTILINE)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.is_intel = "Intel" in self.get_cpuinfo()
        if not self.is_intel and which("sensors") is None:
            self.no_temps = True
        else:
            self.no_temps = False

        self.stat_file = open("/proc/stat", "r")
        atexit_register(self.stat_file.close)

        self.tt0, self.tu0, self.tk0 = self._read_cpu_times()
        self.show_breakdown = False

        # to make sure that cpuinfo is updated at least once on read
        time.sleep(0.01)

        self.q_len = int(2 / self.poll_interval)

        self._usage: dq_t[complex] = deque([], maxlen=self.q_len)
        self._usage_tot: complex = 0.0

        self._times: dq_t[float] = deque([], maxlen=self.q_len)
        self._time_tot: float = 0.0

        self._temps: dq_t[float] = deque([], maxlen=self.q_len)
        self._temp_tot: float = 0.0

    def _read_cpu_times(self) -> Tuple[int, int, int]:
        self.stat_file.seek(0)
        comps = [int(x) for x in self.stat_file.readline().split(" ")[1:] if x]
        return sum(comps), comps[0] + comps[1], comps[2]

    def _read_temp(self) -> float:
        if self.is_intel:
            return self._read_temp_intel()
        else:
            return self._read_temp_amd()

    def _read_temp_amd(self) -> float:
        sensors_raw = check_output("sensors").decode("utf-8")
        t1 = float(self.AMD_RYZEN_PAT1.findall(sensors_raw)[0])
        t2 = float(self.AMD_RYZEN_PAT1.findall(sensors_raw)[0])
        return (t1 + t2) / 2

    def _read_temp_intel(self) -> float:

        temp: float = 0.0
        n_cores = 0

        for fn in glob("/sys/class/thermal/thermal_zone*/temp"):
            with open(fn, "r") as f:
                try:
                    temp += float(f.read()) / 1000
                    n_cores += 1
                except ValueError:
                    continue

        return temp / n_cores

    async def read(self) -> DSA:
        """

        Returns: dict:
            "p_u": (float, "Fraction of cpu time used by userland."),
            "p_k": (float, "Fraction of cpu time used by kernel."),
            "temp_C": (float, "Average cpu temperature."),
            "err_loading": (bool, "The readings are loading."),
            "err_no_temp": (bool, "Temperature could not be read."),
            "err_no_sensors": (bool, "Needs lm_sensors for this CPU."),

        """
        out = {}

        tt, tu, tk = self._read_cpu_times()
        dtt = tt - self.tt0
        dtu = tu - self.tu0
        dtk = tk - self.tk0
        self.tt0, self.tu0, self.tk0 = tt, tu, tk

        usage = dtu + dtk * 1j

        self._time_tot += dtt
        # kernel = imaginary; user = real
        self._usage_tot += usage

        if self.no_temps:
            out["err_no_sensors"] = True
            temp = None
        else:
            try:
                temp = self._read_temp()
                self._temp_tot += temp
            except ZeroDivisionError:
                out["err_no_temp"] = True
                temp = None

        # before append!
        if len(self._usage) == self.q_len:
            self._time_tot -= self._times[0]
            self._usage_tot -= self._usage[0]

        if len(self._temps) == self.q_len:
            self._temp_tot -= self._temps[0]

        self._usage.append(usage)
        self._times.append(dtt)

        if temp is not None:
            self._temps.append(temp)

        use_mean = self._usage_tot / self._time_tot
        out["p_k"] = use_mean.imag
        out["p_u"] = use_mean.real

        if self._temps:
            out["temp_C"] = self._temp_tot / len(self._temps)
        else:
            out["err_no_temp"] = True

        return out

    def format(self, output: DSA) -> str:
        pu = output["p_u"] * 100
        pk = output["p_k"] * 100

        no_temp = output.pop("err_no_temp", False)
        no_sensors = output.pop("err_no_sensors", False)

        if no_sensors:
            temp_str = color("no sensors", RED)
        elif no_temp:
            temp_str = color("unk", ORANGE)
        else:
            temp = output["temp_C"]
            temp_str = f"{mk_tcolor_str(temp)} C"

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

        return f"cpu [{load_str}] [temp {temp_str}]"

    def handle_click(self, *args) -> None:
        self.show_breakdown ^= True
