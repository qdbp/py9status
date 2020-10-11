import os
import re
import subprocess as sbp
import time
from collections import deque
from threading import Event, Thread
from typing import Deque, Optional, Tuple

from py9status.core import (
    GREY,
    ORANGE,
    PY9Unit,
    RED,
    VIOLET,
    WHITE,
    color,
    colorize_float,
    med_mad,
)
from py9status.default_units import DSA


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

    def __init__(self, server, interface, buflen=1000, timeout=5.0) -> None:
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

    def _parse_ping(self, line: str) -> None:
        stats = self.RE_PING_STATS.findall(line)
        if stats:
            self._ping_rtts.appendleft(float(stats[0][1]))
            self._ping_seqs.appendleft(int(stats[0][0]))
            self._ping_status = None
            return
        else:
            self._ping_status = line.strip()

    def _read_loop(self) -> None:
        # burn header line
        self._pipefile.readline()

        while not self._halt.is_set():
            self._parse_ping(self._pipefile.readline())
            self._ping_last_response = time.time()

    def start(self) -> None:
        if self._ping_last_response is not None:
            raise NotImplementedError(
                "Can't restart an old Pinger. "
                "Instantiate a new Pinger to reset state."
            )

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

    def stop(self) -> None:
        self._halt.set()
        self._pipefile.close()
        # NOTE os.kill hangs
        # reap the zombie like this
        self._proc.wait()

    def poll(
        self,
    ) -> Tuple[int, Optional[Tuple[float, float, float, float]]]:

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
                self._ping_seqs[0]
                - self._ping_seqs[-1]
                - len(self._ping_seqs)
                + 1
            ) / len(self._ping_seqs)
            return self.PING_HAVE_STATS, (med, mad, mx, loss)


class PY9Net(PY9Unit):
    """
    Monitor bytes sent and received per unit time on a network interface.
    """

    def __init__(
        self, interface, *args, ping_server="8.8.8.8", **kwargs
    ) -> None:

        """
        Args:
            interface: the interface name to monitor
            ping_server: if not None, will ping this server for latency stats
        """

        super().__init__(*args, **kwargs)
        self.interface = interface
        self.ping_server = ping_server

        self.rx_file = f"/sys/class/net/{interface}/statistics/rx_bytes"
        self.tx_file = f"/sys/class/net/{interface}/statistics/tx_bytes"
        self.operfile = f"/sys/class/net/{interface}/operstate"

        self._rx_dq: Deque[int] = deque([], maxlen=int(2 / self.poll_interval))
        self._tx_dq: Deque[int] = deque([], maxlen=int(2 / self.poll_interval))
        self._time_dq: Deque[float] = deque(
            [], maxlen=int(2 / self.poll_interval)
        )

        self.pinger = None

    def _get_rx_tx(self) -> Tuple[int, int]:
        with open(self.rx_file, "r") as f:
            rx = int(f.read())
        with open(self.tx_file, "r") as f:
            tx = int(f.read())
        return rx, tx

    async def read(self) -> DSA:
        """
        Returns: dict:
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


        """

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
                {
                    "Bps_down": rxr,
                    "Bps_up": txr,
                }
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

    def _format_bw(self, output: DSA) -> str:
        prefix = f"net {self.interface} "

        if output.pop("err_if_gone", False):
            return prefix + color("gone", RED)
        if output.pop("err_if_down", False):
            return prefix + color("down", ORANGE)
        if output.pop("err_if_loading", False):
            return prefix + color("loading", VIOLET)

        sfs = [color("B/s", GREY), color("B/s", GREY)]
        vals = [output["Bps_down"], output["Bps_up"]]

        for ix in range(2):
            for mag, sf in [
                (30, color("G/s", VIOLET)),
                (20, color("M/s", WHITE)),
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

    def _format_ping(self, output: DSA) -> str:
        prefix = f"net {self.interface} [ping {self.ping_server}] "

        if output.pop("err_ping_timeout", False):
            return prefix + color("timed out", RED)
        elif output.pop("err_ping_loading", False):
            return prefix + color("loading", VIOLET)
        elif output.pop("err_ping_fail", False):
            return prefix + color(output["ping_fail_status"], ORANGE)
        else:
            m, std, mx, loss = (
                output["ping_med"],
                output["ping_mad"],
                output["ping_max"],
                output["ping_loss"],
            )

            med_str = colorize_float(m, 4, 1, (10.0, 20.0, 50.0, 100.0))
            mad_str = colorize_float(std, 3, 1, (1.0, 3.0, 9.0, 27.0))
            max_str = colorize_float(mx, 3, 0, (20.0, 50.0, 100.0, 250.0))
            loss_str = colorize_float(
                100 * loss, 4, 1, (1e-4, 1e-1, 1e-0, 5e-0)
            )

            return prefix + (
                f"[med {med_str} mad {mad_str} max {max_str} ms] "
                f"[loss {loss_str}%]"
            )

    def format(self, output: DSA) -> str:
        if output.pop("is_pinging", False):
            return self._format_ping(output)
        else:
            return self._format_bw(output)

    def handle_click(self, *args) -> None:
        if self.pinger is None:
            self._start_ping()
        else:
            self._stop_ping()

    def _start_ping(self) -> None:
        self.pinger = Pinger(self.ping_server, self.interface)
        self.pinger.start()

    def _stop_ping(self) -> None:
        self.pinger.stop()
        self.pinger = None
