#! /usr/bin/python

from collections import deque
import concurrent.futures as cfu
import heapq as hpq
import json
from shutil import which
from sys import stdout, stdin
import time
import traceback as trc

# base 16 tomorrow colors
# https://chriskempson.github.io/base16/#tomorrow

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
    '''
    generates a JSON string snippet corresponding to one i3bar element.

    Args:
        chunk:
    '''
    # TODO: short_text support
    '''
            a string, the `full_text` of the unit's output, or None
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

    assert isinstance(chunk, str)
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
    class managing the control loop.

    contains distinct units which each generate one or more output chunks,
    and are polled for output independently according to their `unit.ival`
    value
    '''

    def __init__(self, units, min_sleep=0.33, padding=1, chunk_kwargs=None):
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
        names = set()
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

        self._unit_q = []
        self._click_q = deque()
        self._exe = cfu.ThreadPoolExecutor(max_workers=8)

        if chunk_kwargs is None:
            self.chunk_kwargs = {}
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

        for u in self.units:
            self._exe.submit(self._exe_unit, u)
            hpq.heappush(self._unit_q, (time.time() + u.ival, u))

    def write_statusline(self):
        '''
        aggregates all units' output into a single string statusline and
        writes it.
        '''
        o = []
        for u in self.units:
            # we don't really care about concurrent modification
            # no synchrony is expected among unit updates
            chunk_json = self.unit_outputs[u.name]
            if chunk_json:
                o.append(chunk_json)

        stdout.write('[' + ','.join(o) + '],\n')
        stdout.flush()

    def _read_clicks(self):
        '''
        "daemon" loop, to run in a separate thread, reading click events
        provided by i3 to stdin and dispatching them to _exe_unit
        '''
        # TODO: maybe find a proper json parser, not this DIY hackery
        while True:
            # "burn" the opening [\n or ,\n
            stdin.read(2)
            try:
                # TODO: ...
                # maybe this would be more reasonable in cython
                # but I don't want to pull in third-party json streamers
                s = ''
                while True:
                    c = stdin.read(1)
                    s += c
                    if c == '}':
                        break

                click = json.loads(s)
                self._exe.submit(self._exe_unit,
                                 self.units_by_name[click.pop('name')],
                                 clicked=True, click=click)

            except Exception:
                continue

    def _exe_unit(self, unit, clicked=False, click=None):
        '''
        execute unit._get_chunk(), updating its most current output
        in self.unit_outputs.

        if clicked is true (and thus click is provided), unit.handle_click
        is addicionally called before _get_chunk invocation. furthermore,
        if `clicked`, the statusline is written immediately after _get_chunk
        returns, so that the user can be given immediate feedback.

        if `_get_chunk` or `process_chunk` raises an uncaught exception,
        the unit enters a failure state, indicated on the status line.
        '''
        # TODO: provide means of unit debugging on fail
        try:
            if clicked:
                assert click is not None
                unit.handle_click(click)
            o = unit._get_chunk()
            self.unit_outputs[unit.name] =\
                process_chunk(unit, o, self.padding, **self.chunk_kwargs)
            # assume statusline is costly enough to process such that
            # having it rewritten on every unit execution would be imprudent
            # hence, we aggregate in unit_outputs, then print in a batch
            # unless the unit has been clicked and needs an immediate update
            if clicked:
                self.write_statusline()
        except Exception:
            trc.print_exc()
            self.unit_outputs[unit.name] =\
                process_chunk(unit,
                              colorify('unit "{}" failed'.format(unit.name),
                                       '#FF0000'),
                              self.padding, **self.chunk_kwargs)

    def run(self):
        '''
        the main control loop.

        units to run next are kept in a priority queue. when a unit is executed
        its next "to run" time is set to `unit.ival + time.time()`
        (NOT `unit.ival` + previous time).
        '''

        # header
        stdout.write('{"version":1,"click_events":true}\n[\n')
        stdout.flush()

        if self.fail:
            stdout.write('[' + self.fail + '],\n')
            stdout.flush()
            while True:
                time.sleep(1e9)

        # start input reader
        self._exe.submit(self._read_clicks)

        while True:

            now = time.time()
            while self._unit_q[0][0] < now:
                t, u = self._unit_q[0]
                # threads - don't GIL on me
                self._exe.submit(self._exe_unit, u)
                hpq.heapreplace(self._unit_q, (now + u.ival, u))

            # writing a statuline is -assumed- somehwat costly on the i3
            # end, therefore we don't just roll it into exe_unit, unless
            # the unit is clicked
            self.write_statusline()

            time.sleep(max(self.min_sleep, self._unit_q[0][0] - time.time()))


class PY9Unit:
    '''
    class producing a single chunk of the status line

    each class is, ideally, documented with an Output API, specifying the
    set of output names which are to be expected and handled by format,
    as output by `unit.read`, which returns dicts of {name: value}
    outputs. That `unit.read` actually adheres to this api is not
    enforced by any code, but should be respected by those wishing to
    write good units.

    Below is a soft specification for how `unit.read` should behave:

     - for the convenience of those overriding `format`, the convention
    that names are prefixed with their type is followed. Common
    prefixed found in default units are:
        i_ for integer,
        f_ for float/double,
        s_ for string
        b_ for boolean

    since these prefixes are purely descriptive, feel free to use them
    as creatively as you see fit.

    - the API can provide for `b_` error flags when expected output
    cannot be produced. As a matter of convention, these flags should be
    checked for first, since other members are not guaranteed to exist
    in the case of an error.

    '''

    api = set('s_info')

    def __init__(self, name=None, ival=1., requires=None):
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
            name = self.__class__.__name__

        self.name = name
        self.ival = ival
        self.transient_overrides = {}
        self.permanent_overrides = {}

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

    def _get_chunk(self):
        '''
        format the unit's output according to the formatting method given

        returns a string which will be taken to be the `full_text` of the
        associated

        the return value should either be a string, which will be assumed to
        be the full_text value of the unit's output, and which permits pango
        markup; or a dict, assumed to conform to the i3bar api and which will
        be serialized as given (pango markup will still be enabled).
        '''
        return self.format(self.read())

    def read(self):
        '''
        get the unit's output in dict format, in line with its api

        read returns a dict, rather than setting internal state as could be
        the case, to avoid concurrency issues.
        '''
        return {'s_info': 'dummy unit output'}

    def format(self, read_output):
        '''
        format the unit's `read` output
        '''
        return '{s_info:}'.format(**read_output)

    def handle_click(self, click):
        '''
        handle the i3-generated `click`, passed as a dictionary. returns None.

        see i3 documentation and example code for click's members
        '''
        self.transient_overrides.update({'border': BASE08})

    # comparison functions for heapq not to crash when times are equal
    def __lt__(self, other):
        return self.name < other.name

    def __ge__(self, other):
        return self.name >= other.name


def mk_tcolor_str(temp):
    if temp < 100:
        tcolor = BASE0B
        if temp > 50:
            tcolor = BASE0A
        elif temp > 75:
            tcolor = BASE09
        elif temp > 90:
            tcolor = BASE08
        tcolor_str = colorify('{:3.0f}'.format(temp), tcolor)
    else:  # we're on fire
        tcolor_str = pangofy('{:3.0f}'.format(temp),
                             color='#FFFFFF', background='#FF0000')

    return tcolor_str


# TODO: make generic color function
def get_mem_color(mem_p):
    if mem_p > 90:
        color = BASE08
    elif mem_p > 75:
        color = BASE09
    elif mem_p > 50:
        color = BASE0A
    else:
        color = BASE0B

    return color


def get_bat_color(bat_p):
    if bat_p > 75:
        color = BASE0B
    elif bat_p > 50:
        color = BASE0A
    elif bat_p > 25:
        color = BASE09
    else:
        color = BASE08

    return color


def get_load_color(load_p):
    if load_p <= 33:
        color = BASE0B
    elif load_p < 66:
        color = BASE0A
    elif load_p < 90:
        color = BASE09
    else:
        color = BASE08

    return color

def get_color(i, midpoints):
    """Chooses appropriate conditional-color for colorify function

    Takes an integer and a list of midpoints (3).
    Compares the value of the integer to those midpoints and selects
    the correct one.
    """

    sorted(midpoints)
    # colors: red-orange, orange, yellow, green
    colors = (BASE08, BASE09, BASE0A, BASE0B)
    counter = 0
    for item in midpoints:
        if i >= item:
            counter += 1
    return colors[counter]


def pangofy(s, **kwargs):
    '''
    applies kwargs to s, pango style, returning a span string
    '''

    a = '<span ' +\
        ' '.join(["{}='{}'".format(k, v)
                  for k, v in kwargs.items()
                  if v is not None]) +\
        '>'
    b = '</span>'

    return a + s + b


def colorify(s, color):
    return pangofy(s, color=color)
