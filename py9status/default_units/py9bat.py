from atexit import register as atexit_register
from collections import deque
from statistics import mean
from typing import Deque as dq_t

from py9status.core import (
    BLUE,
    CYAN,
    GREEN,
    ORANGE,
    PY9Unit,
    RED,
    VIOLET,
    color,
    get_color,
    maybe_int,
)
from py9status.default_units import DSA


class PY9Bat(PY9Unit):
    """
    Monitors battery usage and charging status
    """

    STATUS_DIS = 0
    STATUS_CHR = 1
    STATUS_BAL = 2
    STATUS_FUL = 3
    STATUS_UNK = 4

    def __init__(self, *args, bat_id=0, **kwargs) -> None:
        """
        Args:
            bat_id:
                numerical id of the battery to monitor. will be the
                default of 0 in most cases
        """
        super().__init__(*args, **kwargs)
        self.bat_id = bat_id
        self.min_rem_smooth = None

        self.called = 0

        self._clicked = False
        self._cur_status = None

        self.P_hist: dq_t[float] = deque(
            [], maxlen=int(10 / self.poll_interval)
        )

        self.f_uevent = open(f"/sys/class/power_supply/BAT{bat_id}/uevent")
        atexit_register(self.f_uevent.close)

    def _parse_uevent(self) -> DSA:
        self.f_uevent.seek(0)
        # 13 = len('POWER_SUPPLY_')
        out = {}

        for line in self.f_uevent.readlines():
            k, v = line.strip().split("=")
            out[k[13:].lower()] = maybe_int(v.lower())

        return out

    # noinspection PyPep8Naming
    async def read(self) -> DSA:
        """
        Returns: dict:
            "err_no_bat": (
                bool,
                "The battery corresponding to the given battery id was "
                "not found on the system.",
            ),
            "err_bad_format": (bool, "$bat/uevent had an unrecognized format."),
            "status": (
                int,
                "The current battery status, as given by the STATUS_*"
                "class variables",
            ),
            "sec_rem": (
                float,
                "Seconds remaning until empty/full. -1 if indefinite, "
                "None if loading.",
            ),
            "charged_f": (float, "Fraction charged of current max capacity."),
            "charged_f_design": (
                float,
                "Fraction charged of factory capacity.",
            ),
        }

        """

        # micro-X-hour to SI
        uh_to_si = 0.0036

        self.called += 1
        uevent = self._parse_uevent()

        if not uevent["present"]:
            return {"err_no_bat": True}

        # all units SI
        try:
            # if we have only charges and currents, do the power
            # calculations ourselves.
            if "charge_now" in uevent:
                # given in uAh, convert to C
                Q = uh_to_si * uevent["charge_now"]
                Qmx = uh_to_si * uevent["charge_full"]
                Qmxd = uh_to_si * uevent["charge_full_design"]

                V = uevent["voltage_now"] / 1e6
                Vmn = uevent["voltage_min_design"] / 1e6

                I = uevent["current_now"] / 1e6
                P = I * V

                # assume V(Q) is linear, V(0) = Vmin
                # then Vmxd = Vmin + (Qmxd / Q) * (V - Vmin)
                # then Emxd = Qmx * (V0 + Vmx) / 2

                # NOTE could build a more sophisticated statistical model for
                # V(Q), but that seems needlessly complicated

                Vmx = V * (Qmx / Q)
                Emx = Qmx * (Vmn + Vmx) / 2

                Vmxd = V * (Qmxd / Q)
                Emxd = Qmxd * (Vmn + Vmxd) / 2

                # E(Q), integrating the assumed linear relationship
                E = Q * (Vmn + Q * (Vmxd - Vmn) / (2 * Qmxd))

            else:
                # XXX test these on different models
                P = uevent["power_now"] / 1e6
                # these are given in watt hours, SIfy them
                E = uh_to_si * uevent["energy_now"]
                Emx = uh_to_si * uevent["energy_full"]
                Emxd = uh_to_si * uevent["energy_full_design"]

            charged_f = E / Emx
            charged_f_design = E / Emxd

        except KeyError:
            return {"err_bad_format": True}

        self.P_hist.append(P)
        av_p = mean(self.P_hist)

        out = {"charged_f": charged_f, "charged_f_design": charged_f_design}

        raw_status = uevent["status"]

        if av_p == 0:
            status = self.STATUS_BAL
        elif raw_status == "charging":
            status = self.STATUS_CHR
        elif raw_status == "full":
            status = self.STATUS_FUL
        elif raw_status == "unknown":
            status = self.STATUS_UNK
        elif raw_status == "discharging":
            status = self.STATUS_DIS
        else:
            status = self.STATUS_UNK

        out["status"] = status

        # reset the smoothing if we detect a status change
        if status != self._cur_status:
            self.min_rem_smooth = None
            self._cur_status = status
            self.P_hist.clear()

        if status == self.STATUS_CHR:
            sec_rem = (Emx - E) / av_p
        elif status == self.STATUS_DIS:
            sec_rem = E / av_p
        else:
            sec_rem = -1

        out["sec_rem"] = sec_rem

        return out

    def format(self, info: DSA) -> str:

        e_prefix = f"bat{self.bat_id}" + " [{}]"

        if info.pop("err_no_bat", False):
            return e_prefix.format(color("no bat", RED))

        elif info.pop("err_bad_format", False):
            return e_prefix.format(color("loading", ORANGE))

        # if self._clicked, show % of design capacity instead
        # pct = output['f_chr_pct']

        if self._clicked:
            pct = 100 * info["charged_f_design"]
        else:
            pct = 100 * info["charged_f"]

        pct_str = color(f"{pct:3.0f}", get_color(pct, do_reverse=True))

        st = info["status"]

        if st == self.STATUS_CHR:
            st_string = color("chr", GREEN)
        elif st == self.STATUS_DIS:
            st_string = color("dis", ORANGE)
        elif st == self.STATUS_FUL:
            st_string = color("ful", BLUE)
        elif st == self.STATUS_BAL:
            st_string = color("bal", CYAN)
        else:
            st_string = color("unk", VIOLET)

        raw_sec_rem = info.get("sec_rem")

        if raw_sec_rem is None:
            rem_string = color("loading", VIOLET)
        elif raw_sec_rem < 0:
            rem_string = "--:--"
        else:
            isr = round(raw_sec_rem)
            min_rem = (isr // 60) % 60
            hr_rem = isr // 3600

            rem_string = f"{hr_rem:02d}:{min_rem:02d}"

        x = ["[", "]"] if not self._clicked else ["&lt;", "&gt;"]

        return f"bat {x[0]}{pct_str}%{x[1]} [{rem_string} rem, {st_string}]"

    def handle_click(self, click) -> None:
        self._clicked ^= True
