from datetime import datetime as dtt
from re import findall
from subprocess import check_output
import time

from py3status import PY3Unit, colorify, pangofy,\
    get_load_color, get_mem_color, mk_tcolor_str,\
    BASE08, BASE0E, BASE00, BASE06, BASE0C, BASE09,\
    BASE0D


class PY3Time(PY3Unit):
    '''
    outputs the current time

    Requires:
        date
    '''
    def __init__(self, *args, fmt='%a %b %d %Y - %H:%M', **kwargs):
        '''
        Args:
            fmt:
                the format for strftime to print the date in. Defaults
                to something sensible.
        '''
        self.fmt = fmt
        super().__init__(*args, requires=['date'], **kwargs)

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
    outputs battery usage and charging status

    Requires:
        acpi
    '''

    def __init__(self, *args, bat_id=0, **kwargs):
        '''
        Args:
            bat_id:
                numerical id of the battery to monitor. will be the
                default of 0 in most cases
        '''
        self.bat_id = bat_id
        self.smooth_min_rem = 0
        self.called = 0
        self._p = 1/10
        self._q = 1 - self._p
        super().__init__(*args, requires=['acpi'], **kwargs)

    def get_chunk(self):
        self.called += 1
        prefix = 'bat '
        out = check_output(['acpi', '-bi']).decode('ascii')
        
        # acpi -bi outputs empty string if no battery
        if not out: 
            return prefix + '[' + colorify('no battery', BASE08) + ']'

        line1 = out.split('\n')[0]

        path_id = '/sys/class/power_supply/BAT{}/uevent'.format(self.bat_id)
        with open(path_id, 'r') as f:
            raw = f.read() 
            raw_status = findall('POWER_SUPPLY_STATUS=(\w*)', raw)[0]
            raw_energy = findall('POWER_SUPPLY_ENERGY_NOW=(\d*)', raw)[0]
            # raw_full = findall('POWER_SUPPLY_ENERGY_FULL=(\d*)', raw)[0]
            raw_full_design =\
                findall('POWER_SUPPLY_ENERGY_FULL_DESIGN=(\d*)', raw)[0]
            # raw_capacity= findall('POWER_SUPPLY_CAPACITY=(\d*)', raw)[0]

        # status
        raw_status = findall('POWER_SUPPLY_STATUS=(\w*)', raw)[0]

        if raw_status == "Charging":
            status = "chr"
        elif raw_status == "Full":
            status = "full"
        else:
            status = "dis"

        raw_percentage = int(raw_energy)/int(raw_full_design)*100
        percentage = "{:3.0f}%".format(raw_percentage)

        if 'will never fully discharge' in line1:
            rem = 'inf'
            status = 'bal'
        elif 'Discharging' in line1 or 'Charging' in line1:
            m_rem = int(findall(':([0-9]{2}):', line1)[0])
            h_rem = int(findall('\s([0-9]{2}):', line1)[0])
            # do not alarm user with lowball estimates on startup,
            # give smoother only after it's well-mixed
            cur_min = m_rem + 60*h_rem
            self.smooth_min_rem = (self._p * (cur_min) +
                                   self._q * (self.smooth_min_rem))

            show_min = (cur_min if self.called < 10
                                else int(self.smooth_min_rem))
            rem = '{:02d}:{:02d}'.format(show_min//60, show_min % 60)
        elif 'Charging' in line1:
            m_rem = int(findall(':([0-9]{2}):', line1)[0])
            h_rem = int(findall('\s([0-9]{2}):', line1)[0])
            # do not alarm user with lowball estimates on startup,
            # give smoother only after it's well-mixed
            cur_min = m_rem + 60*h_rem
            self.smooth_min_rem = (self._p * (cur_min) +
                                   self._q * (self.smooth_min_rem))
            
            show_min = (cur_min if self.called < 10
                                else int(self.smooth_min_rem))
            rem = '{:02d}:{:02d}'.format(show_min//60, show_min % 60)
        else:
            rem = "inf"

        # output options: status, percentage, time
        output = prefix + "[{}] [{} rem, {}]".format(percentage, rem, status)

        return output


class PY3Net(PY3Unit):
    '''
    monitor bytes sent and received per unit time on a network interface
    '''
    def __init__(self, i_f, *args, smooth=1/5, **kwargs):
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
                    self.mark = None
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


class PY3Disk(PY3Unit):
    '''
    monitor disk activity
    '''
    def __init__(self, disk, *args, bs=512, **kwargs):
        '''
        Args:
            disk:
                the disk label as found in `/dev/`, e.g. "sda", etc.
            bs:
                the disk block size in bytes, will usually be 512
        '''
        self._no_disk_ival = 10

        self.disk = disk
        self.bs = bs
        self.stat = '/sys/class/block/{}/stat'.format(self.disk)

        super().__init__(*args, **kwargs)

        self.last_r = None
        self.lasw_w = None

    def get_chunk(self):
        # TODO: free space, in flight reading, read magnitudes
        context = 'disk [' + self.disk + ' {}]'
        try:
            with open(self.stat, 'r') as f:
                _, _, r, _, _, _, w, _, ifl, _, _ =\
                    [int(x) for x in f.read().split()]
        except FileNotFoundError:
            self.ival = self._no_disk_ival
            return context.format(colorify('---', BASE08))

        r_fmt = {'color': BASE00}
        w_fmt = {'color': BASE00}

        if self.last_r is not None:
            dr = r - self.last_r
            dw = w - self.last_w
            self.last_r = r
            self.last_w = w
            if dr > 0:
                r_fmt['background'] = BASE0D
            if dw > 0:
                w_fmt['background'] = BASE09
        else:
            self.last_r = r
            self.last_w = w
            return context.format(colorify('loading', BASE0E))

        return context.format('{}{}'.format(pangofy('R', **r_fmt),
                                            pangofy('W', **w_fmt)))
