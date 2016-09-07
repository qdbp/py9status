#! /usr/bin/python

import concurrent.futures as cfu
from datetime import datetime as dtt
import heapq as hpq
import json
from re import findall
from subprocess import check_output
from sys import stdout
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


def chunk_to_json(name, chunk, padding, **kwargs):
    '''
    generates a string snippet corresponding to one i3bar element.

    a single logical unit can generate more than one such element, hence
    separators should be handled by the status_command and not i3bar itself.

    all kwargs are according to the i3 bar api specification
    '''

    # chunks can return None to signify no output
    if chunk is None:
        return ''

    # if a unit just returns text, assume it's the `full_text`:
    if isinstance(chunk, str):
        chunk = {'full_text': chunk}

    # use pango by default
    chunk.update({'markup': 'pango'})

    # if the chunk provides no name, use the unit's name
    if 'name' not in chunk:
        chunk.update({'name': name})

    chunk['full_text'] = padding*' ' + chunk['full_text'] + padding*' '

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

    def __init__(self, units, chunk_padding=2, min_sleep=0.33):
        '''
        units:
            list of units to poll. their ordering in the list will
            order their output.
        chunk_padding:
            number of spaces to draw at the beginning and end of each
            unit's chunk
        min_sleep:
            minimum number of seconds to sleep between unit poll sweeps
        '''
        self.units = units
        self._q = []
        self._exe = cfu.ThreadPoolExecutor(max_workers=4)

        self.chunk_lines = {u.name: u.get_chunk()
                            for u in self.units}
        self.chunk_padding = chunk_padding

        self.min_sleep = min_sleep

        for u in self.units:
            hpq.heappush(self._q, (time.time() + u.ival, u))

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
            chunk_json = chunk_to_json(u.name, self.chunk_lines[u.name],
                                       self.chunk_padding)
            if chunk_json:
                o.append(chunk_json)

        return ','.join(o)

    def _exe_unit(self, unit):
        try:
            o = unit.get_chunk()
            self.chunk_lines[unit.name] = o
        except Exception:
            trc.print_exc()
            self.chunk_lines[unit.name] = None

    def run(self):
        # header
        stdout.write('{"version":1,"click_events":true}\n[\n')
        stdout.flush()

        while True:
            now = time.time()
            while self._q[0][0] < now:
                t, u = self._q[0]
                # threads - don't GIL on me
                self._exe.submit(self._exe_unit, u)
                hpq.heapreplace(self._q, (now + u.ival, u))

            o = '[' + self.get_output() + '],\n'
            stdout.write(o)
            stdout.flush()

            time.sleep(max(self.min_sleep, self._q[0][0] - time.time()))


class PY3Unit:
    def __init__(self, name=None, ival=1.):
        if name is None:
            name = self.__class__.__name__
        self.name = name
        self.ival = ival

    def get_chunk(self):
        '''
        return a dict suitable for input to chunk_to_json.

        allows pango markup.
        '''
        return 'unimplemented unit output'

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


# DEFAULT UNITS

class PY3Time(PY3Unit):
    def get_chunk(self):
        now = dtt.now()
        return now.strftime('%H:%M, %a %b %-m, %Y')


class PY3NVGPU(PY3Unit):
    def get_chunk(self):
        raw = check_output(['nvidia-smi']).decode('ascii')
        line = raw.split('\n')[8]

        temp = int(findall('(?<= )[0-9]{2,3}(?=C )', line)[0])
        mem = int(findall('[0-9]+(?=MiB /)', line)[0])
        mem_tot = int(findall('[0-9]{2,}(?=MiB \|)', line)[0])
        mem_p = 100*mem/mem_tot
        load = int(findall('[0-9]+(?=% +Def)', line)[0])

        ret = ('gpu [mem used {} MiB ({}%)] [load {}%] [temp {}C]'
               .format(colorify('{:6.1f}'.format(mem),
                                get_mem_color(mem_p)),
                       colorify('{:2.0f}'.format(mem_p),
                                get_mem_color(mem_p)),
                       colorify('{:2.0f}'.format(load),
                                get_load_color(load)),
                       mk_tcolor_str(temp)
                       )
               )

        return ret


class PY3CPU(PY3Unit):
    def get_chunk(self):
        out = check_output(['mpstat', '1', '1']).decode('ascii')
        l = out.split('\n')[3]

        load_p = 100 - float(findall(r'[0-9\.]+', l)[-1])

        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = int(f.read())/1000

        color = get_load_color(load_p)
        tcolor_str = mk_tcolor_str(temp)

        return ('cpu [load ' + colorify('{:3.0f}'.format(load_p), color) +
                '%] [temp ' + tcolor_str + 'C]')


class PY3Mem(PY3Unit):
    def get_chunk(self):
        out = check_output(['free', '-m']).decode('ascii')
        l = out.split('\n')[1]
        entries = findall(r'[0-9\.]+', l)

        tot, used = int(entries[0])/(1 << 10), int(entries[1])/(1 << 10)
        p_used = 100*used/tot

        color = get_mem_color(p_used)

        out = ('mem [used ' + colorify('{:2.2f}'.format(used), color) +
               ' GiB (' + colorify('{:3.0f}'.format(p_used), color) +
               '%)]')
        return out


class PY3Net(PY3Unit):
    def __init__(self, i_f, down_ival=30, smooth=1/5, **kwargs):
        super().__init__(**kwargs)
        self.i_f = i_f
        # TODO: we don't ever care if these are closed properly...
        self.rx_file = open('/sys/class/net/{}/statistics/rx_bytes'
                            .format(i_f), 'r')
        self.tx_file = open('/sys/class/net/{}/statistics/tx_bytes'
                            .format(i_f), 'r')
        self.operfile = open('/sys/class/net/{}/operstate'
                             .format(i_f), 'r')
        self.mark = None
        self.down_ival = down_ival
        self.smooth = smooth

    def _get_rx_tx(self):
        rx = int(self.rx_file.read())
        tx = int(self.tx_file.read())
        self.rx_file.seek(0)
        self.tx_file.seek(0)
        return rx, tx

    def get_chunk(self):
        if "down" in self.operfile.read():
            self.operfile.seek(0)
            self.ival = self.down_ival
            return None

        if self.mark is None:
            self.mark = time.time()
            self.old_rx, self.old_tx = self._get_rx_tx()
            self.old_rxr, self.old_txr = 0, 0
            return None
        else:
            rx, tx = self._get_rx_tx()

            now = time.time()
            rxr = self.smooth*(rx - self.old_rx)/(now - self.mark) +\
                (1-self.smooth)*self.old_rxr
            txr = self.smooth*(tx - self.old_tx)/(now - self.mark) +\
                (1-self.smooth)*self.old_txr

            self.old_rx, self.old_tx = rx, tx
            self.old_rxr, self.old_txr = rxr, txr
            self.mark = now

            sfs = ['B/s', 'B/s']
            vals = [rxr, txr]
            for ix in range(2):
                for mag, sf in [(30, 'GiB/s'), (20, 'MiB/s'), (10, 'KiB/s')]:
                    if vals[ix] > 1 << mag:
                        vals[ix] /= 1 << mag
                        sfs[ix] = sf
                        break

        return ('net {} '.format(self.i_f) +
                '[up {:6.1f} {:>5s}] '.format(vals[1], sfs[1]) +
                '[down {:6.1f} {:>5s}] '.format(vals[0], sfs[0]))


def main():
    units = [PY3NVGPU(ival=5.),
             PY3Mem(ival=3.),
             PY3CPU(),
             PY3Net('vpn-ca', name='net_e0', ival=0.5),
             PY3Time(ival=0.7)]
    py3s = PY3Status(units)
    py3s.run()


if __name__ == '__main__':
    main()
