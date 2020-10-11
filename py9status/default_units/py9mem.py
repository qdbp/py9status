from atexit import register as atexit_register

from py9status.core import PY9Unit, color, get_color
from py9status.default_units import DSA


class PY9Mem(PY9Unit):
    """
    Monitors RAM usage.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.f_mem = open("/proc/meminfo", "r")
        atexit_register(self.f_mem.close)

    async def read(self) -> DSA:
        """
        Returns: dict:
            "used_KiB": (int, "Used memory, KiB"),
            "used_frac": (float, "Fraction of memory used"),
        """

        self.f_mem.seek(0)
        memlines = self.f_mem.readlines()
        m_tot_kib = int(memlines[0].split(" ")[-2])
        m_av_kib = int(memlines[2].split(" ")[-2])

        used_kib = m_tot_kib - m_av_kib
        used_p = used_kib / m_tot_kib

        return {
            "used_KiB": used_kib,
            "used_frac": used_p,
        }

    def format(self, output: DSA) -> str:
        mp = output["used_frac"] * 100
        ug = output["used_KiB"] / (1 << 20)

        col = get_color(mp)

        fug = color(f"{ug:4.1f}", col)
        fmp = color(f"{mp:3.0f}", col)

        return f"mem [used {fug} GiB ({fmp}%)]"
