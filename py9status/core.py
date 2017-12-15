#! /usr/bin/python

import asyncio as aio
import bisect
import json
import time
import traceback as trc
from abc import abstractmethod, abstractproperty
from collections import Counter
from shutil import which
from sys import stdin, stdout
from typing import Any, Dict, Set, Tuple, Counter as Ctr_t

# base 16 tomorrow colors
# https://chriskempson.github.io/base16/#tomorrow

# TODO: rename the colors to be human readable
BASE00 = '#1D1F21'
BASE01 = '#282A2E'
BASE02 = '#373B41'
BASE03 = '#969896'
BASE04 = '#B4B7B4'
BASE05 = '#C5C8C6'
BASE06 = '#E0E0E0'
BASE07 = '#FFFFFF'
BASE08 = '#CC6666'
BASE09 = '#DE935F'
BASE0A = '#F0C674'
BASE0B = '#B5BD68'
BASE0C = '#8ABEB7'
BASE0D = '#81A2BE'
BASE0E = '#B294BB'
BASE0F = '#A3685A'

CHUNK_DEFAULTS = {'markup': 'pango',
                  'border': BASE02,
                  'separator': 'false',
                  'separator_block_width': 0}


def process_chunk(unit, chunk, padding, **kwargs):
    # TODO: short_text support
    '''
    Generates a JSON string snippet corresponding to one i3bar element.

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
        unit.transient_overrides (which will be cleared after)
        unit.permament_overrides (which, naturally, will not)
        kwargs ("global" overrides set in the control loop)
    '''

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
    '''
    Class managing the control loop.

    contains distinct units which each generate one or more output chunks,
    and are polled for output independently according to their `unit.ival`
    value
    '''

    def __init__(self, units, min_sleep=0.1, padding=1, chunk_kwargs=None):
        '''
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
        '''

        self.fail = ''
        names: Set[str] = set()
        self.loop = aio.get_event_loop()

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

        self.unit_outputs =\
            {u.name: process_chunk(u,
                                   colorify('unit "%s" loading' % u.name,
                                            BASE0E),
                                   self.padding,
                                   **self.chunk_kwargs
                                   )
             for u in self.units}

    def write_statusline(self):
        '''
        Aggregates all units' output into a single string statusline and
        writes it.
        '''
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

        await self.loop.connect_read_pipe(lambda: rp, stdin)

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
            self.write_statusline()
            await aio.sleep(self.min_sleep)

    def run(self) -> None:
        '''
        The main control loop.
        '''

        # header
        stdout.write('{"version":1,"click_events":true}\n[\n')
        stdout.flush()

        if self.fail:
            stdout.write('[' + self.fail + '],\n')
            stdout.flush()

            while True:
                time.sleep(1e9)

        aio.ensure_future(self.read_clicks(), loop=self.loop)
        for unit in self.units:
            aio.ensure_future(
                unit._main_loop(
                    self.unit_outputs,
                    self.padding,
                    self.chunk_kwargs
                ),
                loop=self.loop,
            )
        aio.ensure_future(self.line_writer())

        self.loop.run_forever()


class PY9Unit:
    '''
    Class producing a single chunk of the status line. Individual units
    should inherit directly from this class.

    Each subclass is documented with an Output API, specifying the
    set of output names of the unit.

    The existence of a `unit.api` @property is enforced, and should yield
    a dictionary of `key: (type, description)` elements. Each key should
    correpond to a key in the dictionary output by `read`. This api should
    be seen as an extended-form docstring for those wishing to override
    `format` without knowing the details of `read`.

    By convention, `read` should indicate failure states through keys
    named `err_*`. `format` should check for these first, as their presence
    might indicate the absence or invalidity of data keys. These errors
    should be documented in the `api`.
    '''

    name_resolver: Ctr_t[str] = Counter()

    def __init__(self, name=None, ival=0.33, requires=None) -> None:
        '''
        Args:
            name:
                name of the unit as seen by i3. if None, will be set to
                the class name. Multiple unnamed instances of the same class
                lead to problems !!!
            ival:
                frequency with which the control loop will try to poll this
                unit. True frequency will be somewhat less
                (see `PY9Status.run`)
            requires:
                list of binaries which are required for this unit to function.
                If any of these is absent, the unit's `_get_chunk`
                method will be replaced with a graceful failure message.
        '''
        # TODO: fix these problems
        '''
        Members:
            self.transient_overrides:
                `process_chunk` will, after each invocation of _get_chunk,
                augment the returned json with these parameters, and clear this
                dict.
            self.permanent_overrides:
                same as above, but `process_chunk` will not clear these.
                subordinate to transient_overrides.
        '''

        if name is None:
            cname = self.__class__.__name__
            name_ix = self.name_resolver[cname]
            name = cname + ('' if name_ix == 0 else f'_{name_ix}')

        self.name = name
        self.ival = ival
        self.transient_overrides: Dict[str, str] = {}
        self.permanent_overrides: Dict[str, str] = {}

        if requires is not None:
            for req in requires:
                if which(req) is None:
                    self._get_chunk =\
                        lambda: (self.name + ' [' +
                                 colorify(req + ' not found', BASE08) +
                                 ']')
                    break

        # TODO: I think the GIL will prevent dict.updates from different
        # threads from exploding, but I'm not sure
        # self.ovr_lock = Lock()

    async def _main_loop(self, d_out, padding, chunk_kwargs):
        while True:
            try:
                d_out[self.name] = process_chunk(
                    self,
                    self.format(self.read()),
                    padding, **chunk_kwargs
                )
            except Exception:
                trc.print_exc()
                d_out[self.name] = process_chunk(
                    self,
                    colorify(f'unit "{self.name}" failed', '#FF0000'),
                    padding,
                    **chunk_kwargs
                )

            await aio.sleep(self.ival)

    @abstractproperty
    def api(self) -> Dict[str, Tuple[type, str]]:
        '''
        Get a dictionary mapping read output keys to their types and
        descriptions.
        '''

    @abstractmethod
    def read(self) -> Dict[str, Any]:
        '''
        Get the unit's output as a dictionary, in line with its API.
        '''

    @abstractmethod
    def format(self, read_output: Dict[str, Any]) -> str:
        '''
        Format the unit's `read` output, returning a string.

        The string will be placed in the "full_text" key of the json sent to
        i3.

        The string may optionally use pango formatting.
        '''

    def handle_click(self, click: Dict[str, Any]) -> None:
        '''
        Handle the i3-generated `click`, passed as a dictionary.

        See i3 documentation and example code for click's members
        '''
        self.transient_overrides.update({'border': BASE08})


def mk_tcolor_str(temp):
    if temp < 100:
        tcolor_str = colorify('{:3.0f}'.format(temp),
                              get_color(temp, breakpoints=[50, 70, 90]))
    else:  # we're on fire
        tcolor_str = pangofy('{:3.0f}'.format(temp),
                             color='#FFFFFF', background='#FF0000')

    return tcolor_str


def get_color(v, breakpoints=[30, 60, 90],
              colors=(BASE0B, BASE0A, BASE09, BASE08), rev=False):
    '''
    Chooses appropriate conditional-color for colorify function.

    Maps an integer and an increasing list of midpoints to a colour in the
    `colors` array based on the integer's index in the list of midpoints.
    '''
    if rev:
        colors = list(reversed(colors))
    return colors[bisect.bisect(breakpoints, v)]


def pangofy(s, **kwargs):
    '''
    applies kwargs to s, pango style, returning a span string
    '''
    a = '<span ' + ' '.join(["{}='{}'".format(k, v) for k, v in kwargs.items()
                             if v is not None]) + '>'
    b = '</span>'
    return a + s + b


def colorify(s, color):
    return pangofy(s, color=color)


def maybe_int(x):
    try:
        return int(x)
    except ValueError:
        return x
