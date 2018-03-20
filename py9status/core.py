#! /usr/bin/python

import asyncio as aio
import bisect
import json
import time
import traceback as trc
from abc import abstractmethod
from collections import Counter
from shutil import which
from sys import stderr, stdin, stdout
from typing import Any, Counter as Ctr_t, Dict, Set, Tuple

# base 16 tomorrow colors
# https://chriskempson.github.io/base16/#tomorrow

NEAR_BLACK = '#1D1F21'
DARKER_GREY = '#282A2E'
DARK_GREY = '#373B41'
GREY = '#969896'
LIGHT_GREY = '#B4B7B4'
LIGHTER_GREY = '#C5C8C6'
NEAR_WHITE = '#E0E0E0'
WHITE = '#FFFFFF'
RED = '#CC6666'
ORANGE = '#DE935F'
YELLOW = '#F0C674'
GREEN = '#B5BD68'
CYAN = '#8ABEB7'
BLUE = '#81A2BE'
VIOLET = '#B294BB'
BROWN = '#A3685A'

CHUNK_DEFAULTS = {
    'markup': 'pango',
    'border': DARK_GREY,
    'separator': 'false',
    'separator_block_width': 0
}

LOOP = aio.get_event_loop()


def process_chunk(unit: "PY9Unit", chunk, padding, **kwargs):
    # TODO: short_text support
    """
    Generates a JSON string snippet corresponding to the output one i3bar
    unit.

    Args:
        chunk:
            A string, the `full_text` of the unit's output, or `None`.
        padding:
            number of spaces to add at the beginning and end of each unit's
            text
        kwargs:
            any valid i3bar input API keyword. Takes precedence over
            default values.

    Returns:
        a string containing JSON output expected by the i3bar API for a single
        bar element.

    Will override defaults with, in decreasing order of precedence,
        `unit.transient_overrides` (which will be cleared after)
        `unit.permanent_overrides` (which, naturally, will not)
        kwargs ("global" overrides set in the control loop)
    """

    # chunks can return None to signify no output
    if chunk is None:
        return ''

    chunk = {'full_text': chunk}

    # change some defaults:
    chunk.update(CHUNK_DEFAULTS)

    # set the name
    chunk.update({'name': unit.name})

    # apply any global (kwarg) overrides
    chunk.update(kwargs)
    # apply any unit-set overrides
    chunk.update(unit.permanent_overrides)
    # transient overrides take precedence
    chunk.update(unit.transient_overrides)
    unit.transient_overrides.clear()

    chunk['full_text'] = ' ' * padding + chunk['full_text'] + ' ' * padding

    return json.dumps(chunk)


class PY9Status:
    """
    Class managing the control loop.

    contains distinct units which each generate one or more output chunks,
    and are polled for output independently according to their `unit.ival`
    value
    """

    def __init__(self, units, min_sleep=0.1, padding=1, chunk_kwargs=None):
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

        self.fail = ''
        names: Set[str] = set()

        for u in units:
            if u.name not in names:
                names.add(u.name)
                continue
            self.fail = json.dumps(
                {'full_text': colorify('GLOBAL FAILURE: duplicate unit name %s'
                                       % u.name, '#FF0000'),
                    'markup': 'pango'
                })
            break

        self.units = units
        self.units_by_name = {u.name: u for u in units}

        if chunk_kwargs is None:
            self.chunk_kwargs: Dict[str, Any] = {}
        else:
            assert isinstance(chunk_kwargs, dict)
            self.chunk_kwargs = chunk_kwargs
        self.padding = padding

        self.min_sleep = min_sleep

        self.unit_outputs = {
            u.name: process_chunk(
                u,
                colorify('unit "%s" loading' % u.name, VIOLET),
                self.padding,
                **self.chunk_kwargs
            )
            for u in self.units
        }

    def write_status_line(self):
        """
        Aggregates all units' output into a single string status line and
        writes it.
        """
        o = []
        for u in self.units:
            chunk_json = self.unit_outputs[u.name]
            if chunk_json:
                o.append(chunk_json)

        stdout.write('[' + ','.join(o) + '],\n')
        stdout.flush()

    async def read_clicks(self):
        rt = aio.StreamReader()
        rp = aio.StreamReaderProtocol(rt)

        await LOOP.connect_read_pipe(lambda: rp, stdin)

        # we can get by without a json parser for this stream, carefully...
        # "burn" the opening [\n or ,\n
        await rt.read(2)

        while True:
            try:
                raw = await rt.readuntil(b'}')
                click = json.loads(raw)
                self.units_by_name[click.pop('name')].handle_click(click)
                # burn the comma
                await rt.readuntil(b',')
            except Exception:
                continue

    async def line_writer(self):
        while True:
            self.write_status_line()
            await aio.sleep(self.min_sleep)

    def run(self) -> None:
        """
        The main control loop.
        """

        # header
        stdout.write('{"version":1,"click_events":true}\n[\n')
        stdout.flush()

        if self.fail:
            stdout.write('[' + self.fail + '],\n')
            stdout.flush()

            while True:
                time.sleep(1e9)

        aio.ensure_future(self.read_clicks(), loop=LOOP)
        for unit in self.units:
            aio.ensure_future(
                unit.main_loop(
                    self.unit_outputs,
                    self.padding,
                    self.chunk_kwargs
                ),
                loop=LOOP,
            )
        aio.ensure_future(self.line_writer())

        LOOP.run_forever()


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

    def __init__(self, name=None, poll_interval=0.33, requires=None) -> None:
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
        name += ('' if name_ix == 0 else f'_{name_ix}')
        self.name = name

        self.poll_interval = poll_interval

        self.transient_overrides: Dict[str, str] = {}
        self.permanent_overrides: Dict[str, str] = {}

        if requires is not None:
            for req in requires:
                if which(req) is None:
                    self._get_chunk = \
                        lambda: (self.name + ' [' +
                                 colorify(req + ' not found', RED) +
                                 ']')
                    break

        self._fail = False

    async def main_loop(self, d_out, padding, chunk_kwargs):
        while True:
            try:
                if self._fail:
                    raise ValueError
                d_out[self.name] = process_chunk(
                    self,
                    self.format(self.read()),
                    padding, **chunk_kwargs
                )
            except Exception:
                if self._fail:
                    fail_str = colorify(self._fail, BROWN)
                else:
                    fail_str = colorify(f'unit "{self.name}" failed', BROWN)
                trc.print_exc(file=stderr)
                d_out[self.name] = process_chunk(
                    self, fail_str, padding,
                    **chunk_kwargs
                )

            await aio.sleep(self.poll_interval)

    @property
    @abstractmethod
    def api(self) -> Dict[str, Tuple[type, str]]:
        """
        Get a dictionary mapping read output keys to their types and
        descriptions.
        """

    @abstractmethod
    def read(self) -> Dict[str, Any]:
        """
        Get the unit's output as a dictionary, in line with its API.
        """

    @abstractmethod
    def format(self, read_output: Dict[str, Any]) -> str:
        """
        Format the unit's `read` output, returning a string.

        The string will be placed in the "full_text" key of the json sent to
        i3.

        The string may optionally use pango formatting.
        """

    def handle_click(self, click: Dict[str, Any]) -> None:
        """
        Handle the i3-generated `click`, passed as a dictionary.

        See i3 documentation and example code for click's members
        """
        self.transient_overrides.update({'border': RED})


def mk_tcolor_str(temp):
    if temp < 100:
        tcolor_str = colorify('{:3.0f}'.format(temp),
                              get_color(temp, breakpoints=[30, 50, 70, 90]))
    else:  # we're on fire
        tcolor_str = pangofy('{:3.0f}'.format(temp),
                             color='#FFFFFF', background='#FF0000')

    return tcolor_str


def get_color(v, breakpoints=None,
              colors=(BLUE, GREEN, YELLOW, ORANGE, RED), rev=False):
    """
    Chooses appropriate conditional-color for colorify function.

    Maps an integer and an increasing list of midpoints to a colour in the
    `colors` array based on the integer's index in the list of midpoints.
    """
    if breakpoints is None:
        breakpoints = [20, 40, 60, 80]
    if rev:
        colors = list(reversed(colors))
    return colors[bisect.bisect(breakpoints, v)]


def pangofy(s, **kwargs):
    """
    applies kwargs to s, pango style, returning a span string
    """
    a = '<span ' + ' '.join(["{}='{}'".format(k, v) for k, v in kwargs.items()
                                if v is not None]) + '>'
    b = '</span>'
    return a + s + b


def colorify(s, color):
    return pangofy(s, color=color)


def colorize_float(val, length, prec, breakpoints):
    return colorify(
        f'{val:{length}.{prec}f}', get_color(val, breakpoints=breakpoints))


def maybe_int(x):
    try:
        return int(x)
    except ValueError:
        return x
