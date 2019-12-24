import os
import re
import subprocess as sbp
import time
from atexit import register as exreg
from bisect import bisect
from collections import deque
from datetime import datetime as dtt
from glob import glob
from multiprocessing import cpu_count
from re import findall
from statistics import mean
from typing import Deque as dq_t
from typing import Dict, Tuple

from .core import (
    BLUE,
    BROWN,
    CYAN,
    GREEN,
    GREY,
    ORANGE,
    RED,
    VIOLET,
    WHITE,
    PY9Unit,
    colorify,
    colorize_float,
    format_duration,
    get_color,
    maybe_int,
    med_mad,
    mk_tcolor_str,
)


class PY9Time(PY9Unit):
    """
    Displays the current time2
    """

    @property
    def api(self):
        return {
            "datestr": (str, "Current datetime in unit's format"),
            "uptime": (int, "Current uptime, in seconds"),
            "loadavg_1": (float, "One minute load average [as in `uptime`]"),
            "loadavg_5": (float, "Five minute load average [as in `uptime`]"),
            "loadavg_10": (float, "Ten minute load average [as in `uptime`]"),
        }

    def __init__(self, *args, fmt="%a %b %d %Y - %H:%M", **kwargs):
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
        self._load_color_scale = tuple(_cpu_count * x for x in [0.1, 0.25, 0.50, 0.75])

        super().__init__(*args, **kwargs)

    def _read_uptime(self):
        with open(self.uptime_fn) as uf:
            return float(uf.read().split(" ")[0])

    def _read_loadavg(self):
        with open(self.loadavg_fn) as lf:
            return tuple(map(float, lf.read().split(" ")[:3]))

    def read(self):
        now = dtt.now()
        l1, l5, l10 = self._read_loadavg()

        return {
            "datestr": now.strftime(self.fmt),
            "uptime": self._read_uptime(),
            "loadavg_1": l1,
            "loadavg_5": l5,
            "loadavg_10": l10,
        }

    def format(self, output):
        if not self._doing_uptime:
            return output["datestr"]
        else:
            ut_s = format_duration(output["uptime"])
            lss = [
                colorize_float(output["loadavg_" + key], 3, 2, self._load_color_scale)
                for key in ["1", "5", "10"]
            ]

            return f"uptime [{ut_s}] load [{lss[0]}/{lss[1]}/{lss[2]}]"

    def handle_click(self, *args):
        self._doing_uptime = not self._doing_uptime


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, requires=["nvidia-smi"], **kwargs)

    def read(self):
        raw = sbp.check_output(["nvidia-smi"]).decode("ascii")
        line = raw.split("\n")[8]

        temp_C = int(findall("(?<= )[0-9]{2,3}(?=C )", line)[0])
        mem_mib = int(findall("[0-9]+(?=MiB /)", line)[0])
        mem_tot = int(findall("[0-9]{2,}(?=MiB \|)", line)[0])
        mem_pct = int(100 * mem_mib / mem_tot)
        load_pct = int(findall("[0-9]+(?=% +Def)", line)[0])

        return {
            "i_mem_mib": mem_mib,
            "i_mem_pct": mem_pct,
            "i_load_pct": load_pct,
            "i_temp_C": temp_C,
        }

    def format(self, output):
        mm = output["i_mem_mib"]
        mp = output["i_mem_pct"]
        lp = output["i_load_pct"]
        temp = output["i_temp_C"]
        return "gpu [mem used {} MiB ({}%)] [load {}%] [temp {}C]".format(
            colorify("{: 4d}".format(mm), get_color(mp)),
            colorify("{: 3d}".format(mp), get_color(mp)),
            colorify("{: 3d}".format(lp), get_color(lp)),
            mk_tcolor_str(temp),
        )


# TODO: error handling
class PY9CPU(PY9Unit):
    """
    Monitors CPU usage and temperature.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.pstat = open("/proc/stat", "r")
        exreg(self.pstat.close)

        self.tt0, self.tu0, self.tk0 = self._read_cpu_times()
        self.show_breakdown = False

        # to make sure that cpuinfo is updated at least once on read
        time.sleep(0.01)

        self._us: dq_t[float] = deque([], maxlen=int(2 / self.poll_interval))
        self._ks: dq_t[float] = deque([], maxlen=int(2 / self.poll_interval))
        self._temps: dq_t[int] = deque([], maxlen=int(2 / self.poll_interval))

    @property
    def api(self):
        return {
            "p_u": (float, "Fraction of cpu time used by userland."),
            "p_k": (float, "Fraction of cpu time used by kernel."),
            "temp_C": (float, "Average cpu temperature."),
            "err_no_temp": (bool, "Temperature could not be read."),
        }

    def _read_cpu_times(self):
        self.pstat.seek(0)

        comps = [int(x) for x in self.pstat.readline().split(" ")[1:] if x]

        return sum(comps), comps[0] + comps[1], comps[2]

    def read(self):
        tt, tu, tk = self._read_cpu_times()
        dtt = tt - self.tt0
        dtu = tu - self.tu0
        dtk = tk - self.tk0
        self.tt0, self.tu0, self.tk0 = tt, tu, tk

        self._us.append(dtu / dtt)
        self._ks.append(dtk / dtt)

        out = {"p_k": mean(self._ks), "p_u": mean(self._us)}

        temp = 0.0
        n_cores = 0
        # XXX assumes this exists
        for fn in glob("/sys/class/thermal/thermal_zone*/temp"):
            with open(fn, "r") as f:
                try:
                    this_temp = float(f.read()) / 1000
                    if this_temp > 0:
                        temp += this_temp
                        n_cores += 1
                except Exception:
                    pass

        if temp is not None:
            temp /= n_cores
            self._temps.append(temp)
            out["temp_C"] = mean(self._temps)
        else:
            self._temps.clear()
            out["err_no_temp"] = True

        return out

    def format(self, output):
        pu = output["p_u"] * 100
        pk = output["p_k"] * 100

        no_temp = output.pop("err_no_temp", False)

        if no_temp:
            tcolor_str = colorify("unk", ORANGE)
        else:
            temp = output["temp_C"]
            tcolor_str = mk_tcolor_str(temp)

        if self.show_breakdown:
            load_str = (
                "u "
                + colorify(f"{pu:3.0f}", get_color(pu))
                + "% k"
                + colorify(f"{pk:3.0f}", get_color(pk))
                + "%"
            )
        else:
            load_str = "load " + colorify(f"{pu + pk:3.0f}%", get_color(pu + pk))

        return "cpu [" + load_str + "] [temp " + tcolor_str + "C]"

    def handle_click(self, *args):
        self.show_breakdown = not self.show_breakdown


class PY9Mem(PY9Unit):
    """
    monitor memory usage

    Requires:
        free

    Output API:
        'f_used_G':  used memory, gigabytes
        'i_used_pct': used memory, %
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.f_mem = open("/proc/meminfo", "r")
        exreg(self.f_mem.close)

    @property
    def api(self):
        # FIXME add detailed onclick info for buffers, etc
        return {
            "used_KiB": (int, "Used memory, KiB"),
            "used_frac": (float, "Fraction of memory used"),
        }

    def read(self):
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

    def format(self, output):
        mp = output["used_frac"] * 100
        ug = output["used_KiB"] / (1 << 20)

        color = get_color(mp)

        return (
            "mem [used "
            + colorify("{:4.1f}".format(ug), color)
            + " GiB ("
            + colorify("{:3.0f}".format(mp), color)
            + "%)]"
        )


class PY9Bat(PY9Unit):
    """
    Shows battery usage and charging status.
    """

    STATUS_DIS = 0
    STATUS_CHR = 1
    STATUS_BAL = 2
    STATUS_FUL = 3
    STATUS_UNK = 4

    def __init__(self, *args, bat_id=0, **kwargs):
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

        self.P_hist: dq_t[float] = deque([], maxlen=int(10 / self.poll_interval))

        self.f_uevent = open(f"/sys/class/power_supply/BAT{bat_id}/uevent")
        exreg(self.f_uevent.close)

    def _parse_uevent(self):
        self.f_uevent.seek(0)
        # 13 = len('POWER_SUPPLY_')
        out = {}

        for line in self.f_uevent.readlines():
            k, v = line.strip().split("=")
            out[k[13:].lower()] = maybe_int(v.lower())

        return out

    @property
    def api(self):
        return {
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
            "charged_f_design": (float, "Fraction charged of factory capacity."),
        }

    def read(self):

        # micro-X-hour to SI
        uhtosi = 0.0036

        self.called += 1
        ued = self._parse_uevent()

        if not ued["present"]:
            return {"err_no_bat": True}

        # all units SI
        try:
            # if we have only charges and currents, do the power
            # calculations ourselves.
            if "charge_now" in ued:
                # given in uAh, convert to C
                Q = uhtosi * ued["charge_now"]
                Qmx = uhtosi * ued["charge_full"]
                Qmxd = uhtosi * ued["charge_full_design"]

                V = ued["voltage_now"] / 1e6
                Vmn = ued["voltage_min_design"] / 1e6

                I = ued["current_now"] / 1e6
                P = I * V

                # assume V(Q) is linear, V(0) = Vmin, then
                Vmx = Vmn + Qmx * (V - Vmn) / Q
                Vmxd = Vmn + Qmxd * (V - Vmn) / Q
                # and, integrating E(q)dq,
                E = Q * (Vmn + V) / 2
                Emx = Qmx * (Vmn + Vmx) / 2
                Emxd = Qmxd * (Vmn + Vmxd) / 2

            else:
                # XXX test these on different models
                P = ued["power_now"] / 1e6
                # these are given in watt hours, SIfy them
                E = uhtosi * ued["energy_now"]
                Emx = uhtosi * ued["energy_full"]
                Emxd = uhtosi * ued["energy_full_design"]

            charged_f = E / Emx
            charged_f_design = E / Emxd

        except KeyError:
            return {"err_bad_format": True}

        self.P_hist.append(P)
        av_p = mean(self.P_hist)

        out = {"charged_f": charged_f, "charged_f_design": charged_f_design}

        raw_status = ued["status"]

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

    def format(self, info):
        e_prefix = f"bat{self.bat_id}" + " [{}]"

        if info.pop("err_no_bat", False):
            return e_prefix.format(colorify("no bat", RED))

        elif info.pop("err_bad_format", False):
            return e_prefix.format(colorify("loading", ORANGE))

        # if self._clicked, show % of design capacity instead
        # pct = output['f_chr_pct']

        if self._clicked:
            pct = 100 * info["charged_f_design"]
        else:
            pct = 100 * info["charged_f"]

        pct_str = colorify(f"{pct:3.0f}", get_color(pct, rev=True))

        st = info["status"]

        if st == self.STATUS_CHR:
            st_string = colorify("chr", GREEN)
        elif st == self.STATUS_DIS:
            st_string = colorify("dis", ORANGE)
        elif st == self.STATUS_FUL:
            st_string = colorify("ful", BLUE)
        elif st == self.STATUS_BAL:
            st_string = colorify("bal", CYAN)
        else:
            st_string = colorify("unk", VIOLET)

        raw_sec_rem = info.get("sec_rem")

        if raw_sec_rem is None:
            rem_string = colorify("loading", VIOLET)
        elif raw_sec_rem < 0:
            rem_string = "--:--"
        else:
            isr = round(raw_sec_rem)
            min_rem = (isr // 60) % 60
            hr_rem = isr // 3600

            rem_string = f"{hr_rem:02d}:{min_rem:02d}"

        x = "[]" if not self._clicked else ["&lt;", "&gt;"]

        return f"bat {x[0]}{pct_str}%{x[1]} [{rem_string} rem, {st_string}]"

    def handle_click(self, click):
        self._clicked = not self._clicked


class PY9Wireless(PY9Unit):
    """
    Provide wireless network information.

    Output API:
        's_SSID': SSID of the connected network
        'f_quality': connection quality, %

    Error API:
        'err_b_down': wireless interface is down
        'err_b_disconnected': connected to network?

    Requires:
        wireless-tools
    """

    @property
    def api(self) -> Dict[str, Tuple[type, str]]:
        return {
            "ssid": (str, "SSID of the connected network."),
            "quality": (float, "Connection quality, from 0 to 1."),
            "err_down": (bool, "True if the wireless interface is down."),
            "err_disconnected": (bool, "True if there is no network connection."),
        }

    def __init__(self, wlan_if, *args, **kwargs):
        """
        Args:
            wlan_if: name of the wireless interface to monitor.
        """

        self.wlan_if = wlan_if
        super().__init__(*args, requires=["iwconfig"], **kwargs)

    def read(self):
        # Future: read stats from /proc/net/wireless?
        # Raw
        out = sbp.check_output(["iwconfig", self.wlan_if]).decode("ascii")
        # line1 = out.split('\n')[0]

        # Status
        # No device detected case
        if "No such device" in out:  # if not connected: 'No such device'
            return {"err_down": True}
        # Not connected case

        if "off/any" in out:  # if not connected: 'ESSID:off/any'
            return {"err_disconnected": True}

        # Raw output data
        raw_SSID = findall('ESSID:"(.*?)"', out)[0]

        n, d = findall("Link Quality=(\d+)/(\d+)", out)[0]
        quality = float(n) / float(d)

        return {"ssid": raw_SSID, "quality": quality}

    def format(self, output):
        prefix = "wlan {} [".format(self.wlan_if)
        suffix = "]"
        if output.pop("err_down", False):
            return prefix + colorify("down", RED) + suffix
        elif output.pop("err_disconnected", False):
            return prefix + colorify("---", VIOLET) + suffix
        else:
            template = prefix + "{}] [{}%" + suffix
            quality = 100 * output["quality"]
            q_color = get_color(quality, rev=True)
            q_str = colorify("{:3.0f}".format(quality), q_color)
            return template.format(output["ssid"], q_str)


class PY9Net(PY9Unit):
    """
    Monitor bytes sent and received per unit time on a network interface.
    """

    class Pinger:
        """
        Class providing a simple interface to the system ping command.

        Spawns a ping process in a background thread and provides a method to
        query its output in a cleaned form.
        """

        PING_TIMEOUT = 0
        PING_LOADING = 1
        PING_HAVE_STATUS = 2
        PING_HAVE_STATS = 3

        RE_PING_STATS = re.compile(
            r"icmp_seq=([0-9]+) ttl=[0-9]+ time=([0-9]+(?:\.[0-9]+)?) ms"
        )

        def __init__(self, server, interface, buflen=1000, timeout=5.0):
            """
            Args:
                server: the server to ping
                interface: the interface to ping on
                buflen: length of statistics buffer to keep
                timeout: time to wait before returning a "pings dropped" message
            """
            self.server = server
            self.interface = interface
            self.buflen = buflen
            self.timeout = timeout

            self._halt = Event()
            self._proc = None
            self._pipefile = None
            self._thread = None

            self._ping_rtts = deque([], maxlen=buflen)
            self._ping_seqs = deque([], maxlen=buflen)
            self._ping_status = None
            self._ping_last_response = None

        def _parse_ping_into_bufs(self, line):
            stats = self.RE_PING_STATS.findall(line)
            if stats:
                self._ping_rtts.appendleft(float(stats[0][1]))
                self._ping_seqs.appendleft(int(stats[0][0]))
                self._ping_status = None
                return
            else:
                self._ping_status = line.strip()

        def _read_loop(self):
            # burn header line
            self._pipefile.readline()

            while not self._halt.is_set():
                self._parse_ping_into_bufs(self._pipefile.readline())
                self._ping_last_response = time.time()

        def start(self):
            if self._ping_last_response is not None:
                raise NotImplementedError("Instantiate a new Pinger to reset state.")

            self._ping_last_response = time.time()

            _read_pipe, _write_pipe = os.pipe()

            self._proc = sbp.Popen(
                ["ping", "-I", self.interface, "-i", "0.2", self.server],
                stdout=_write_pipe,
                stderr=_write_pipe,
                shell=False,
            )
            self._pipefile = os.fdopen(_read_pipe)
            self._thread = Thread(daemon=True, target=self._read_loop)
            self._thread.start()

        def stop(self):
            self._halt.set()
            self._pipefile.close()
            # NOTE os.kill hangs
            # reap the zombie like this
            self._proc.wait()

        def poll(self):
            if time.time() - self._ping_last_response > self.timeout:
                return self.PING_TIMEOUT, None

            elif self._ping_status is not None:
                return self.PING_HAVE_STATUS, self._ping_status

            elif len(self._ping_seqs) < 3:
                return self.PING_LOADING, None

            else:
                med, mad = med_mad(self._ping_rtts)
                mx = max(self._ping_rtts)
                loss = (
                    self._ping_seqs[0] - self._ping_seqs[-1] - len(self._ping_seqs) + 1
                ) / len(self._ping_seqs)
                return self.PING_HAVE_STATS, (med, mad, mx, loss)

    @property
    def api(self):
        return {
            "err_if_down": (bool, "The named interface is down."),
            "err_if_gone": (bool, "The named interface does not exist."),
            "err_if_loading": (
                bool,
                "Currently loading statistics for the " "interface.",
            ),
            "Bps_down": (float, "Bytes per second, ingress"),
            "Bps_up": (float, "Bytes per second, egress"),
            "is_pinging": (bool, "Currently pinging."),
            "err_ping_loading": (bool, "Ping stats are loading."),
            "err_ping_timeout": (bool, "No pings come back within timeout."),
            "err_ping_fail": (bool, "Pings fail with a particular status."),
            "ping_fail_status": (str, "Ping status line, if pings failing."),
            "ping_med": (float, "Median ping time, ms."),
            "ping_mad": (float, "Ping median absolute deviation."),
            "ping_max": (float, "Ping max."),
            "ping_loss": (float, "Ping packet loss."),
        }

    def __init__(self, interface, *args, ping_server="8.8.8.8", **kwargs):
        """
        Args:
            interface: the interface name
        """

        super().__init__(*args, **kwargs)
        self.interface = interface
        self.ping_server = ping_server

        # FIXME we can in principle keep these files open, we just need
        # special exception handing for cases when the interface goes down
        # and then back up
        self.rx_file = f"/sys/class/net/{interface}/statistics/rx_bytes"
        self.tx_file = f"/sys/class/net/{interface}/statistics/tx_bytes"
        self.operfile = f"/sys/class/net/{interface}/operstate"

        self._rx_dq: dq_t[int] = deque([], maxlen=int(2 / self.poll_interval))
        self._tx_dq: dq_t[int] = deque([], maxlen=int(2 / self.poll_interval))
        self._time_dq: dq_t[int] = deque([], maxlen=int(2 / self.poll_interval))

        self.pinger = None

    def _get_rx_tx(self):
        with open(self.rx_file, "r") as f:
            rx = int(f.read())
        with open(self.tx_file, "r") as f:
            tx = int(f.read())
        return rx, tx

    def read(self):
        try:
            with open(self.operfile, "r") as f:
                if "down" in f.read():
                    self._rx_dq.clear()
                    self._tx_dq.clear()
                    self._time_dq.clear()
                    return {"err_if_down": True}
        except OSError:
            return {"err_if_gone": True}

        rx, tx = self._get_rx_tx()

        self._rx_dq.append(rx)
        self._tx_dq.append(tx)
        self._time_dq.append(time.time())

        out = {}

        if len(self._time_dq) < 2:
            out.update({"err_if_loading": True})
        else:
            dt = self._time_dq[-1] - self._time_dq[0]
            rxd = self._rx_dq[-1] - self._rx_dq[0]
            txd = self._tx_dq[-1] - self._tx_dq[0]

            rxr = rxd / dt
            txr = txd / dt

            out.update(
                {"Bps_down": rxr, "Bps_up": txr,}
            )

        if self.pinger is not None:
            out.update({"is_pinging": True})

            status, data = self.pinger.poll()

            if status == self.pinger.PING_TIMEOUT:
                out.update({"err_ping_timeout": True})
            elif status == self.pinger.PING_HAVE_STATUS:
                out.update({"err_ping_fail": True, "ping_fail_status": data})
            elif status == self.pinger.PING_LOADING:
                out.update({"err_ping_loading": True})
            else:
                out.update(
                    {
                        "ping_med": data[0],
                        "ping_mad": data[1],
                        "ping_max": data[2],
                        "ping_loss": data[3],
                    }
                )

        return out

    def _format_bw(self, output):
        prefix = f"net {self.interface} "

        if output.pop("err_if_gone", False):
            return prefix + colorify("gone", RED)
        if output.pop("err_if_down", False):
            return prefix + colorify("down", ORANGE)
        if output.pop("err_if_loading", False):
            return prefix + colorify("loading", VIOLET)

        sfs = [colorify("B/s", GREY), colorify("B/s", GREY)]
        vals = [output["Bps_down"], output["Bps_up"]]

        for ix in range(2):
            for mag, sf in [
                (30, colorify("G/s", VIOLET)),
                (20, colorify("M/s", WHITE)),
                (10, "K/s"),
            ]:
                if vals[ix] > 1 << mag:
                    vals[ix] /= 1 << mag
                    sfs[ix] = sf
                    break

        return (
            prefix
            + f"[u {vals[1]:6.1f} {sfs[1]:>3s}] "
            + f"[d {vals[0]:6.1f} {sfs[0]:>3s}]"
        )

    def _format_ping(self, output):
        prefix = f"net {self.interface} [ping {self.ping_server}] "

        if output.pop("err_ping_timeout", False):
            return prefix + colorify("timed out", RED)
        elif output.pop("err_ping_loading", False):
            return prefix + colorify("loading", VIOLET)
        elif output.pop("err_ping_fail", False):
            return prefix + colorify(output["ping_fail_status"], ORANGE)
        else:
            m, std, mx, loss = (
                output["ping_med"],
                output["ping_mad"],
                output["ping_max"],
                output["ping_loss"],
            )

            med_str = colorize_float(m, 4, 1, [10.0, 20.0, 50.0, 100.0])
            mad_str = colorize_float(std, 3, 1, [3.0, 9.0, 27.0, 81.0])
            max_str = colorize_float(mx, 3, 0, [20.0, 50.0, 100.0, 250.0])
            loss_str = colorize_float(100 * loss, 4, 1, [1e-4, 1e-1, 1e-0, 5e-0])

            return prefix + (
                f"[med {med_str} mad {mad_str} max {max_str} ms] " f"[loss {loss_str}%]"
            )

    def format(self, output):
        if output.pop("is_pinging", False):
            return self._format_ping(output)
        else:
            return self._format_bw(output)

    def handle_click(self, *args):
        if self.pinger is None:
            self._start_ping()
        else:
            self._stop_ping()

    def _start_ping(self):
        self.pinger = self.Pinger(self.ping_server, self.interface)
        self.pinger.start()

    def _stop_ping(self):
        self.pinger.stop()
        self.pinger = None


class PY9Disk(PY9Unit):
    """
    Monitors disk activity.
    """

    BARS = [" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    THRESHS = [1.0] + [1 << i for i in range(10, 24, 2)]

    def __init__(self, disk, *args, **kwargs):
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

    def _get_sector_size(self):
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

    @property
    def api(self):
        return {
            "err_no_disk": (bool, "The given disk or parttion was not found."),
            "bps_read": (float, "Bytes per second read from disk"),
            "bps_write": (float, "Bytes per second written to disk"),
        }

    def read(self):

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

    def format(self, info):

        context = "disk [" + self.disk + " {}]"

        if info.pop("err_no_disk", False):
            return context.format(colorify("absent", BROWN))

        rbar = self.BARS[bisect(self.THRESHS, info["bps_read"])]
        wbar = self.BARS[bisect(self.THRESHS, info["bps_write"])]

        return context.format(colorify(rbar, BLUE) + colorify(wbar, ORANGE))
