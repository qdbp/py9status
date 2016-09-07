#! /usr/bin/python

import concurrent.futures as cfu
from datetime import datetime as dtt
import heapq as hpq
import json
from re import findall
from subprocess import check_output
from sys import stdout
import time

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


def chunk_to_json(u, chunk, **kwargs):
    '''
    generates a string snippet corresponding to one i3bar element.

    a single logical unit can generate more than one such element, hence
    separators should be handled by the status_command and not i3bar itself.

    all kwargs are according to the i3 bar api specification
    '''

    # if a unit just returns text, assume it's the `full_text`:
    if isinstance(chunk, str):
        chunk = {'full_text': chunk}

    # use pango by default
    chunk.update({'markup': 'pango'})

    # if the chunk provides no name, use the unit's name
    if 'name' not in chunk:
        chunk.update({'name': u.name})

    chunk.update(kwargs)

    return json.dumps(chunk)


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


class PY3Status:
    '''
    class managing the entire status line output.
    
    contains distinct units which each generate one or more output chunks,
    and are polled for output independently according to their `unit.ival`
    value
    '''

    def __init__(self, units, loop_ival=0.1):
        '''
        units:
            list of units to poll. their ordering in the list will
            order their output.
        self.units:
        '''
        self.units = units
        self._q = []
        self._exe = cfu.ThreadPoolExecutor(max_workers=4)

        self.chunk_lines = {u.name: chunk_to_json(u, u.get_chunk())
                            for u in self.units}

        for u in self.units:
            hpq.heappush(self._q, (time.time() + u.ival, u))

        self.loop_ival = loop_ival

    def add_unit(self, unit, where=None):
        if where is not None:
            self.units.insert(where, unit)
        else:
            self.units.append(unit)

    def del_unit(self, unit):
        ix = self.units.index(unit)
        del self.units[ix]
        del self.chunk_lines[unit.name]

    def get_output(self):
        o = []
        for u in self.units:
            o.append(self.chunk_lines[u.name])

        return ','.join(o)

    def _exe_unit(self, unit):
        o = chunk_to_json(unit, unit.get_chunk())
        self.chunk_lines[unit.name] = o

    def run(self):
        # header
        stdout.write('{"version":1,"click_events":true}\n[\n')
        stdout.flush()

        while True:
            while self._q[0][0] < time.time():
                t, u = self._q[0]
                self._exe.submit(self._exe_unit, u)
                # self.chunk_lines[u.name] = chunk_to_json(u, u.get_chunk())
                hpq.heapreplace(self._q, (t + u.ival, u))

            o = '[' + self.get_output() + '],\n'
            stdout.write(o)
            stdout.flush()

            time.sleep(self.loop_ival)


class PY3Unit:
    def __init__(self, name, ival=1.):
        self.name = name
        self.ival = ival

    def get_chunk(self):
        '''
        return a dict suitable for input to chunk_to_json.

        allows pango markup.
        '''
        return 'unimplemented unit output'


# DEFAULT UNITS

class PY3Time(PY3Unit):
    def get_chunk(self):
        now = dtt.now()
        ret = now.strftime('%H:%M, %a %b %-m, %Y')
        # ret = check_output(['date']).decode('ascii').strip('\n')
        return ret


class PY3CPU(PY3Unit):
    def get_chunk(self):
        out = check_output(['mpstat', '1', '1']).decode('ascii')
        l = out.split('\n')[3]
        used = 100 - float(findall(r'[0-9\.]+', l)[-1])

        if used <= 33:
            color = BASE0B
        elif used > 33:
            color = BASE0A
        elif used > 66:
            color = BASE09
        elif used > 90:
            color = BASE08

        return 'cpu ' + colorify('{:3.0f}'.format(used), color) + '% used'


class PY3Mem(PY3Unit):
    def get_chunk(self):
        out = check_output(['free', '-m']).decode('ascii')
        l = out.split('\n')[1]
        entries = findall(r'[0-9\.]+', l)
        tot, used = int(entries[0])/(1 << 10), int(entries[1])/(1 << 10)
 
        out = ('mem {:2.2f} GiB [{:3.0f}%] free'
               .format(tot-used, 100*(tot-used)/tot))
        return out


def main():
    units = [PY3Mem('mem'), PY3CPU('cpu'), PY3Time('time', ival=0.7)]
    py3s = PY3Status(units)
    py3s.run()


if __name__ == '__main__':
    main()
