#! /usr/bin/python
from __future__ import annotations

import asyncio as aio
import bisect
import json
import time
import traceback as trc
from abc import abstractmethod
from asyncio import FIRST_COMPLETED
from collections import Counter
from datetime import timedelta
from functools import lru_cache
from math import floor, log10
from numbers import Real
from shutil import which
from statistics import median
from sys import stderr, stdin, stdout
from typing import Any
from typing import Counter as Ctr_t
from typing import Iterable, NoReturn, Optional, TypeVar, final

T = TypeVar("T")
N = TypeVar("N", int, float)

# base 16 tomorrow colors
# https://chriskempson.github.io/base16/#tomorrow

NEAR_BLACK = "#1D1F21"
DARKER_GREY = "#282A2E"
DARK_GREY = "#373B41"
GREY = "#969896"
LIGHT_GREY = "#B4B7B4"
LIGHTER_GREY = "#C5C8C6"
NEAR_WHITE = "#E0E0E0"
WHITE = "#FFFFFF"
RED = "#CC6666"
ORANGE = "#DE935F"
YELLOW = "#F0C674"
GREEN = "#B5BD68"
CYAN = "#8ABEB7"
BLUE = "#81A2BE"
VIOLET = "#B294BB"
BROWN = "#A3685A"

CHUNK_DEFAULTS = {
    "markup": "pango",
    "border": DARK_GREY,
    "separator": "false",
    "separator_block_width": 0,
}


class PY9Status:
    """
    Class managing the control loop.

    contains distinct units which each generate one or more output chunks,
    and are polled for output independently according to their `unit.ival`
    value
    """

    def __init__(
        self, units, min_sleep=0.1, padding=1, chunk_kwargs=None
    ) -> None:
        """
        units:
            list of PY9Unit units to poll. their ordering in the list will
            order their output.
        padding:
            number of spaces to add at the beginning and end of each unit's
            output text
        min_sleep:
            minimum number of seconds to sleep between unit poll sweeps.
        format_kwargs:
            kwargs to pass to `process_chunk`, which formats unit output
            into the format expected by i3. Globally verride `process_chunk`
            defaults with this. Units also have means of doing this on an
            individual basis. see PY9Unit.
        """

        self.fail = ""
        names: set[str] = set()

        for u in units:
            if u.name not in names:
                names.add(u.name)
                continue
            self.fail = json.dumps(
                {
                    "full_text": color(
                        "GLOBAL FAILURE: duplicate unit name %s" % u.name,
                        "#FF0000",
                    ),
                    "markup": "pango",
                }
            )
            break

        self.units = units
        self.units_by_name = {u.name: u for u in units}

        if chunk_kwargs is None:
            self.chunk_kwargs: dict[str, Any] = {}
        else:
            assert isinstance(chunk_kwargs, dict)
            self.chunk_kwargs = chunk_kwargs
        self.padding = padding

        self.min_sleep = min_sleep

        self.unit_outputs = {
            u.name: u.process_chunk(
                color('unit "%s" loading' % u.name, VIOLET),
                self.padding,
                **self.chunk_kwargs,
            )
            for u in self.units
        }

    def write_status_line(self) -> None:
        """
        Aggregates all units' output into a single string status line and
        writes it.
        """

        o = [self.unit_outputs[u.name] for u in self.units]
        stdout.write("[" + ",".join(it for it in o if it is not None) + "],\n")
        stdout.flush()

    async def read_clicks(self) -> NoReturn:
        rt = aio.StreamReader()
        rp = aio.StreamReaderProtocol(rt)

        await aio.get_event_loop().connect_read_pipe(lambda: rp, stdin)

        # we can get by without a json parser for this stream, carefully...
        # "burn" the opening [\n or ,\n
        await rt.read(2)

        while True:
            try:
                raw = await rt.readuntil(b"}")
                click = json.loads(raw)
                # noinspection PyProtectedMember
                self.units_by_name[click.pop("name")]._handle_click(click)
                # burn the comma
                await rt.readuntil(b",")
            finally:
                continue

    async def line_writer(self) -> NoReturn:
        while True:
            self.write_status_line()
            await aio.sleep(self.min_sleep)

    def run(self) -> NoReturn:
        """
        The main control loop.
        """

        # header
        stdout.write('{"version":1,"click_events":true}\n[\n')
        stdout.flush()

        if self.fail:
            stdout.write("[" + self.fail + "],\n")
            stdout.flush()

            while True:
                time.sleep(1e9)

        aio.get_event_loop().create_task(self.read_clicks())
        for unit in self.units:
            aio.get_event_loop().create_task(
                unit.main_loop(
                    self.unit_outputs, self.padding, self.chunk_kwargs
                ),
            )
        aio.get_event_loop().create_task(self.line_writer())

        aio.get_event_loop().run_forever()


class PY9Unit:
    """
    Class producing a single chunk of the status line. Individual units
    should inherit directly from this class.

    Each subclass is documented with an Output API, specifying the
    set of output names of the unit.

    The existence of a `unit.api` @property is enforced, and should yield
    a dictionary of `key: (type, description)` elements. Each key should
    correspond to a key in the dictionary output by `read`. This api should
    be seen as an extended-form docstring for those wishing to override
    `format` without knowing the details of `read`.

    By convention, `read` should indicate failure states through keys
    named `err_*`. `format` should check for these first, as their presence
    might indicate the absence or invalidity of data keys. These errors
    should be documented in the `api`.
    """

    name_resolver: Ctr_t[str] = Counter()

    def __init__(
        self, name=None, poll_interval=0.33, requires=None, **kwargs
    ) -> None:
        """
        Args:
            name:
                name of the unit as seen by i3. if None, will be set to
                the class name. Multiple unnamed instances of the same class
                lead to problems !!!
            poll_interval:
                frequency with which the control loop will try to poll this
                unit. True frequency will be somewhat less
                (see `PY9Status.run`)
            requires:
                list of binaries which are required for this unit to function.
                If any of these is absent, the unit's `_get_chunk`
                method will be replaced with a graceful failure message.

        Attributes:
            self.transient_overrides:
                `process_chunk` will, after each invocation of _get_chunk,
                augment the returned json with these parameters, and clear this
                dict.
            self.permanent_overrides:
                same as above, but `process_chunk` will not clear these.
                subordinate to transient_overrides.
        """

        name = name or self.__class__.__name__

        name_ix = self.name_resolver[name]
        self.name_resolver[name] += 1
        name += "" if name_ix == 0 else f"_{name_ix}"
        self.name = name

        self.poll_interval = poll_interval
        # backwards compatibility
        if "ival" in kwargs:
            self.poll_interval = kwargs.pop("ival")
        if kwargs:
            raise ValueError(f"Got unknown arguments {kwargs.keys()}!")

        self.transient_overrides: dict[str, str] = {}
        self.permanent_overrides: dict[str, str] = {}

        if requires is not None:
            for req in requires:
                if which(req) is None:
                    self._get_chunk = lambda: (
                        self.name + " [" + color(req + " not found", RED) + "]"
                    )
                    break

        self._fail: Optional[str] = None

        # used to prod the main loop awake on user click
        self._wakeup = aio.Event()

    def process_chunk(self, chunk: Optional[str], pad: int, **kwargs):
        # TODO: short_text support
        """
        Generates a JSON string snippet corresponding to the output one i3bar
        unit.

        Args:
            chunk:
                A string, the `full_text` of the unit's output, or `None`.
            pad:
                number of spaces to add at the beginning and end of each unit's
                text
            kwargs:
                any valid i3bar input API keyword. Takes precedence over
                default values.

        Returns:
            a string containing JSON output expected by the i3bar API for a
            single bar element.

        Will override defaults with, in decreasing order of precedence,
            `unit.transient_overrides` (which will be cleared after)
            `unit.permanent_overrides` (which, naturally, will not)
            kwargs ("global" overrides set in the control loop)
        """

        # chunks can return None to signify no output
        if chunk is None:
            return ""

        out = {"full_text": chunk}

        # change some defaults:
        out.update(CHUNK_DEFAULTS)

        # set the name
        out.update({"name": self.name})

        # apply any global (kwarg) overrides
        out.update(kwargs)
        # apply any unit-set overrides
        out.update(self.permanent_overrides)
        # transient overrides take precedence
        out.update(self.transient_overrides)
        self.transient_overrides.clear()

        out["full_text"] = f"{' ' * pad}{out['full_text']}{' ' * pad}"

        return json.dumps(out)

    async def main_loop(
        self, d_out: dict[str, str], padding: int, chunk_kwargs: dict[str, Any]
    ) -> NoReturn:
        while True:
            # noinspection PyBroadException
            try:
                if self._fail:
                    raise ValueError

                d_out[self.name] = self.process_chunk(
                    self.format(await self.read()),
                    padding,
                    **chunk_kwargs,
                )

            except:
                if self._fail:
                    fail_str = color(self._fail, BROWN)
                else:
                    fail_str = color(f'unit "{self.name}" failed', BROWN)

                trc.print_exc(file=stderr)

                d_out[self.name] = self.process_chunk(
                    fail_str, padding, **chunk_kwargs
                )

            finally:
                await aio.wait(
                    [
                        aio.create_task(it)
                        for it in (
                            aio.sleep(self.poll_interval),
                            self._wakeup.wait(),
                        )
                    ],
                    return_when=FIRST_COMPLETED,
                )
                self._wakeup.clear()

    @abstractmethod
    async def read(self) -> dict[str, Any]:
        """
        Get the unit's output as a dictionary, in line with its API.
        """

    @abstractmethod
    def format(self, read_output: dict[str, Any]) -> str:
        """
        Format the unit's `read` output, returning a string.

        The string will be placed in the "full_text" key of the json sent to
        i3.

        The string may optionally use pango formatting.
        """

    @final
    def _handle_click(self, click: dict[str, Any]):
        self.handle_click(click)
        self._wakeup.set()

    def handle_click(self, click: dict[str, Any]) -> None:
        """
        Handle the i3-generated `click`, passed as a dictionary.

        See i3 documentation and example code for click's members
        """
        self.transient_overrides.update({"border": RED})


def mk_tcolor_str(temp: int | float) -> str:
    if temp < 100:
        out = color(
            "{:3.0f}".format(temp),
            get_color(temp, breakpoints=(30, 50, 70, 90)),
        )
    else:  # we're on fire
        out = pangofy(
            "{:3.0f}".format(temp), color="#FFFFFF", background="#FF0000"
        )

    return out


def get_color(
    value: float | int,
    breakpoints: tuple[Real, ...] = (20, 40, 60, 80),
    colors: tuple[str, ...] = (BLUE, GREEN, YELLOW, ORANGE, RED),
    do_reverse=False,
) -> str:
    """
    Chooses appropriate conditional-color for color function.

    Maps an integer and an increasing list of midpoints to a colour in the
    `colors` array based on the integer's index in the list of midpoints.
    """

    assert len(colors) - len(breakpoints) == 1

    if do_reverse:
        colors = list(reversed(colors))

    return colors[bisect.bisect(list(breakpoints), value)]


def pangofy(s: str, **kwargs) -> str:
    """
    applies kwargs to s, pango style, returning a <span> element
    """

    a = (
        "<span "
        + " ".join(
            ["{}='{}'".format(k, v) for k, v in kwargs.items() if v is not None]
        )
        + ">"
    )
    b = "</span>"

    return a + s + b


def color(s: str, color: str) -> str:
    return pangofy(s, color=color)


def colorize_float(
    val: float, length: int, prec: int, breakpoints: tuple[Real, ...]
):
    return color(
        f"{val:{length}.{prec}f}", get_color(val, breakpoints=breakpoints)
    )


@lru_cache(maxsize=1024)
def format_duration(val: timedelta | Real) -> str:
    """
    Formats a duration in seconds in a human-readable way.

    Has a fixed width of 9.
    """

    if isinstance(val, timedelta):
        val = val.seconds + 1e-6 * val.microseconds

    if val < 60:
        if val < 1e-9:
            unit = "ps"
            display_val = val * 1e12
        elif val < 1e-6:
            unit = "ns"
            display_val = val * 1e9
        elif val < 1e-3:
            unit = "us"
            display_val = val * 1e6
        elif val < 1.0:
            unit = "ms"
            display_val = val * 1e3
        else:
            unit = "s"
            display_val = val

        precision = max(0, 2 - floor(log10(display_val)))

        return f"  {display_val: >3.{precision}f} {unit} "

    # val (- [minute, four weeks)
    elif 60 <= val < 3155760000:
        if val < 3600:
            fst, snd_s = divmod(val, 60)
            snd = int(snd_s)
            first_unit, second_unit = "m", "s"
        elif val < 86400:
            fst, snd_s = divmod(val, 3600)
            snd = int(snd_s / 60)
            first_unit, second_unit = "h", "m"
        elif val < 604800:
            fst, snd_s = divmod(val, 86400)
            snd = int(snd_s / 3600)
            first_unit, second_unit = "d", "h"
        elif val < 31557600:
            fst, snd_s = divmod(val, 604800)
            snd = int(snd_s / 86400)
            first_unit, second_unit = "w", "d"
        else:
            fst, snd_s = divmod(val, 31557600)
            snd = int(snd_s / 604800)
            first_unit, second_unit = "y", "w"

        return f"{int(fst): >2d} {first_unit} {snd: >2d} {second_unit}"

    else:
        return " > 10 y  "


def maybe_int(x: T) -> T | int:
    """
    Converts a value to an int if possible, else returns the input unchanged.
    """
    try:
        return int(x)
    except ValueError:
        return x


def med_mad(xs: Iterable[N]) -> tuple[N, N]:
    """
    Returns the median and median absolute deviation of the passed iterable.
    """

    med = median(xs)
    mad = median(abs(x - med) for x in xs)

    return med, mad
