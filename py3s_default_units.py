from datetime import datetime as dtt
from re import findall
from subprocess import check_output
import time

from py3status import PY3Unit, colorify,\
    get_load_color, get_mem_color, mk_tcolor_str,\
    BASE08, BASE0E


class PY3Time(PY3Unit):
    '''
    outputs the current time

    Requires:
        date
    '''
    def __init__(self, *args, fmt='%H:%M, %a %b %-m, %Y', **kwargs):
        '''
        Args:
            fmt:
                the format for strftime to print the date in. Defaults
                to something sensible.
        '''
        super().__init__(*args, **kwargs)
        self.fmt = fmt
        # we just assume date is there

    def get_chunk(self):
        now = dtt.now()
        return now.strftime(self.fmt)


class PY3NVGPU(PY3Unit):
    '''
    monitors a nvidia gpu.

    Requires:
        nvidia-smi
    '''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, requires=['nvidia-smi'], **kwargs)

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
    '''
    monitors CPU usage and temperature

    Requires:
        mpstat (sysstat)
    '''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, requires=['mpstat'], **kwargs)

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
    '''
    monitor memory usage

    Requires:
        free
    '''
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

class PY3Bat(PY3Unit):
    '''
    outputs battery usage

    Requires:
        acpi
    '''

    def get_chunk(self):
        out = check_output(['acpi', '-bi']).decode('ascii')

        line1 = out.split('\n')[0]
        line2 = out.split('\n')[1]
#        acpi -b makes line2 unnecessary
        
        status = findall('Battery 0: (\w*?),', line1)[0]
        percentage = findall('Battery 0: \w*?, (\d*)%', line1)[0]
        time = findall('Battery 0: \w*?, \d*%, ([\w:]*)\s', line1)[0]
        
        if status == "Charging":
            status2 = "CHR"
            if int(percentage) >100:
                status2 = "FULL" #need to figure out behaviour here for fully charged
        else:
            status2 = "BAT"
        
        # output = status + percentage + time
        #output = findall('Battery 0: (\w*?), (\d*)%, ([\w:]*)\s', line1)
        output = "{} {}% {}".format(status2, percentage, time)
        
        return output


class PY3Net(PY3Unit):
    '''
    monitor bytes sent and received per unit time on a network interface
    '''
    def __init__(self, i_f, *args, down_ival=30, smooth=1/5, **kwargs):
        '''
        Args:
            i_f:
                the interface name
            down_ival:
                if the interface is found to be down,
                set self.ival to this value to slow down polling
            smooth:
                constant a for [a * X_t + (1 - a) * X_tm1] IIR smoothing
                of the displayed rate
        '''

        super().__init__(*args, **kwargs)
        self.i_f = i_f

        self.rx_file = '/sys/class/net/{}/statistics/rx_bytes'.format(i_f)
        self.tx_file = '/sys/class/net/{}/statistics/tx_bytes'.format(i_f)
        self.operfile = '/sys/class/net/{}/operstate'.format(i_f)
        self.mark = None
        self.down_ival = down_ival
        self.smooth = smooth

    def _get_rx_tx(self):
        with open(self.rx_file, 'r') as f:
            rx = int(f.read())
        with open(self.tx_file, 'r') as f:
            tx = int(f.read())
        return rx, tx

    def get_chunk(self):
        prefix = 'net {} '.format(self.i_f)
        try:
            with open(self.operfile, 'r') as f:
                if "down" in f.read():
                    self.ival = self.down_ival
                    return prefix + colorify('down', BASE08)
        except FileNotFoundError:
            return prefix + colorify('down', BASE08)

        if self.mark is None:
            self.mark = time.time()
            self.old_rx, self.old_tx = self._get_rx_tx()
            self.old_rxr, self.old_txr = 0, 0
            return prefix + colorify('loading', BASE0E)
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
                for mag, sf in [(30, 'G/s'), (20, 'M/s'), (10, 'K/s')]:
                    if vals[ix] > 1 << mag:
                        vals[ix] /= 1 << mag
                        sfs[ix] = sf
                        break

        return (prefix +
                '[u {:6.1f} {:>3s}] '.format(vals[1], sfs[1]) +
                '[d {:6.1f} {:>3s}] '.format(vals[0], sfs[0]))
