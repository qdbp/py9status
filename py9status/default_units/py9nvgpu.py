from asyncio import subprocess as asbp
from re import findall

from py9status.core import PY9Unit, color, get_color, mk_tcolor_str
from py9status.default_units import DSA


class PY9NVGPU(PY9Unit):
    """
    monitors a nvidia gpu.

    Requires:
        nvidia-smi

    Output API:
        'i_mem_mib': GPU memory used, MiB
        'i_mem_pct': GPU memoru used, %
        'i_load_pct': GPU load, %
        'i_temp_C': GPU temperature, deg C
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, requires=["nvidia-smi"], **kwargs)

    async def read(self) -> DSA:
        proc = await asbp.create_subprocess_shell(
            "nvidia-smi", stdout=asbp.PIPE
        )
        raw, _ = await proc.communicate()
        out = raw.decode("ascii")
        line = out.split("\n")[8]

        temp_C = int(findall(r"(?<= )[0-9]{2,3}(?=C )", line)[0])
        mem_mib = int(findall(r"[0-9]+(?=MiB /)", line)[0])
        mem_tot = int(findall(r"[0-9]{2,}(?=MiB \|)", line)[0])
        mem_pct = int(100 * mem_mib / mem_tot)
        load_pct = int(findall(r"[0-9]+(?=% +Def)", line)[0])

        return {
            "i_mem_mib": mem_mib,
            "i_mem_pct": mem_pct,
            "i_load_pct": load_pct,
            "i_temp_C": temp_C,
        }

    def format(self, output: DSA) -> str:
        mm = output["i_mem_mib"]
        mp = output["i_mem_pct"]
        lp = output["i_load_pct"]
        temp = output["i_temp_C"]
        return "gpu [mem used {} MiB ({}%)] [load {}%] [temp {}C]".format(
            color("{: 4d}".format(mm), get_color(mp)),
            color("{: 3d}".format(mp), get_color(mp)),
            color("{: 3d}".format(lp), get_color(lp)),
            mk_tcolor_str(temp),
        )
