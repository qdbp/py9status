from collections import deque
from datetime import datetime as dtt
from re import findall
from subprocess import check_output
import time

from .py9core import (PY9Unit, colorify, pangofy,  # noqa \
    get_color, mk_tcolor_str,
    BASE00, BASE01, BASE02, BASE03, BASE04, BASE05, BASE06, BASE07,
    BASE08, BASE09, BASE0A, BASE0B, BASE0C, BASE0D, BASE0E, BASE0F)


class PY9Time(PY9Unit):
    '''
    outputs the current time

    Requires:
        date

    Output API:
        's_datestr': date string formatted according to `fmt`
    '''
    # TODO: turn apis into a class member Enum

    def __init__(self, *args, fmt='%a %b %d %Y - %H:%M', **kwargs):
        '''
        Args:
            fmt:
                the format for strftime to print the date in. Defaults
                to something sensible.
        '''
        self.fmt = fmt
        super().__init__(*args, requires=['date'], **kwargs)

    def read(self):
        now = dtt.now()
        return {'s_datestr': now.strftime(self.fmt)}

    def format(self, output):
        return output['s_datestr']


class PY9NVGPU(PY9Unit):
    '''
    monitors a nvidia gpu.

    Requires:
        nvidia-smi

    Output API:
        'i_mem_mib': GPU memory used, MiB
        'i_mem_pct': GPU memoru used, %
        'i_load_pct': GPU load, %
        'i_temp_C': GPU temperature, deg C
    '''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, requires=['nvidia-smi'], **kwargs)

    def read(self):
        raw = check_output(['nvidia-smi']).decode('ascii')
        line = raw.split('\n')[8]

        temp_C = int(findall('(?<= )[0-9]{2,3}(?=C )', line)[0])
        mem_mib = int(findall('[0-9]+(?=MiB /)', line)[0])
        mem_tot = int(findall('[0-9]{2,}(?=MiB \|)', line)[0])
        mem_pct = int(100 * mem_mib / mem_tot)
        load_pct = int(findall('[0-9]+(?=% +Def)', line)[0])

        return {'i_mem_mib': mem_mib,
                'i_mem_pct': mem_pct,
                'i_load_pct': load_pct,
                'i_temp_C': temp_C}

    def format(self, output):
        mm = output['i_mem_mib']
        mp = output['i_mem_pct']
        lp = output['i_load_pct']
        temp = output['i_temp_C']
        return ('gpu [mem used {} MiB ({}%)] [load {}%] [temp {}C]'
                .format(colorify('{: 4d}'.format(mm),
                                 get_color(mp)),
                        colorify('{: 3d}'.format(mp),
                                 get_color(mp)),
                        colorify('{: 3d}'.format(lp),
                                 get_color(lp)),
                        mk_tcolor_str(temp)
                        )
                )


# TODO: error handling
class PY9CPU(PY9Unit):
    '''
    monitors CPU usage and temperature

    Requires:
        mpstat (sysstat)

    Output API:
        'i_load_pct': CPU load percentage
        'i_temp_C': CPU temperature, deg C
    '''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, requires=['mpstat'], **kwargs)

    def read(self):
        # TODO: implement smoothing
        out = check_output(['mpstat', '1', '1']).decode('ascii')
        l = out.split('\n')[3]

        pcts = findall(r'[0-9\.]+', l)
        # - idle - cpuwait
        load_p = 100 - float(pcts[-1]) - float(pcts[-7])

        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = int(f.read()) // 1000

        return {'i_load_pct': load_p,
                'i_temp_C': temp}

    def format(self, output):
        lp = output['i_load_pct']
        temp = output['i_temp_C']

        color = get_color(lp)
        tcolor_str = mk_tcolor_str(temp)

        return ('cpu [load ' + colorify('{:3.0f}'.format(lp), color) +
                '%] [temp ' + tcolor_str + 'C]')


class PY9Mem(PY9Unit):
    '''
    monitor memory usage

    Requires:
        free

    Output API:
        'f_used_G':  used memory, gigabytes
        'i_used_pct': used memory, %
    '''

    def read(self):
        out = check_output(['free', '-m']).decode('ascii')
        l = out.split('\n')[1]
        entries = findall(r'[0-9\.]+', l)

        tot, used = int(entries[0]) / (1 << 10), int(entries[1]) / (1 << 10)
        p_used = 100 * used / tot

        return {'f_used_G': used,
                'i_used_pct': p_used}

        return out

    def format(self, output):
        mp = output['i_used_pct']
        ug = output['f_used_G']

        color = get_color(mp)

        return ('mem [used ' + colorify('{:4.1f}'.format(ug), color) +
                ' GiB (' + colorify('{:3.0f}'.format(mp), color) +
                '%)]')


class PY9Bat(PY9Unit):
    '''
    outputs battery usage and charging status

    Output API:
        'b_chr': True if charging
        'b_dis': True if discharging
        'b_bal': True if balanced, not full (plugged in, no net inflow)
        'b_full': True if full
        'i_min_rem': minutes until (dis)charged, -1 if infinite
        'f_chr_pct': percentage charge of battery with respect to current full
         capacity
        'f_chr_pct_design': with respect to design capacity

    Error API:
        'b_error_no_bat': True if the battery with the given ID can't be found,
            or more precisely, if the uevent file cannot be read found
        'b_error_unknown_format': True if the battery's uevent could be read
            but had an unrecognized format.
    '''
    # TODO: add more; e.g. full/design full, etc.

    def __init__(self, *args, bat_id=0, **kwargs):
        '''
        Args:
            bat_id:
                numerical id of the battery to monitor. will be the
                default of 0 in most cases
        '''
        super().__init__(*args, **kwargs)
        self.bat_id = bat_id
        self.min_rem_smooth = None
        self.called = 0
        self._p = 1 / 5
        self._q = 1 - self._p

        self._clicked = False

        self._cur_status = None

    def handle_click(self, click):
        self._clicked = not self._clicked

    def _get_power_metrics(self, raw):
        gauges = ['ENERGY', 'CHARGE']
        for gauge in gauges:
            # fill is charge if charge is read, else it is energy
            # TODO: consider refactoring to output units power only
            # the barrier to that is that max_energy_d is not obvious
            # in the voltage/current case
            # (is it CHARGE_FULL_D * VOLTAGE_MIN_D ?)
            try:
                cur_fill = int(findall(r'POWER_SUPPLY_{}_NOW=(\d+)'
                                       .format(gauge), raw)[0])
                max_fill = int(findall(r'POWER_SUPPLY_{}_FULL=(\d+)'
                                       .format(gauge), raw)[0])
                max_fill_design =\
                    int(findall(r'POWER_SUPPLY_{}_FULL_DESIGN=(\d+)'
                                .format(gauge), raw)[0])
                break
            except IndexError:
                continue
        else:
            raise ValueError()
        # drain is in "fill units"; i.e. "power" if energy is read directly
        # or "current" if charge/voltage is read
        try:
            drain = int(findall(r'POWER_SUPPLY_POWER_NOW=(\d+)', raw)[0])
        except IndexError:
            drain = int(findall(r'POWER_SUPPLY_CURRENT_NOW=(\d+)', raw)[0])

        return cur_fill, max_fill, max_fill_design, drain

    def read(self):
        self.called += 1
        fn_uevent = '/sys/class/power_supply/BAT{}/uevent'.format(self.bat_id)
        try:
            with open(fn_uevent, 'r') as f:
                raw = f.read()
        except (FileNotFoundError, IOError):
            return {'b_error_no_bat': True}

        raw_status = findall(r'POWER_SUPPLY_STATUS=(\w+)', raw)[0]
        try:
            cur_fill, max_fill, max_fill_d, drain =\
                self._get_power_metrics(raw)
        except ValueError:
            return {'b_error_unkown_format': True}

        out = {'b_chr': False, 'b_dis': False, 'b_bal': False, 'b_full': False}

        if raw_status == "Charging":
            out['b_chr'] = True
            status = 'chr'
        elif raw_status == "Full":
            out['b_full'] = True
            status = 'ful'
        elif drain == 0:
            out['b_bal'] = True
            status = 'bal'
        else:
            out['b_dis'] = True
            status = 'dis'

        # reset the smoothing if we detect a status change
        if status != self._cur_status:
            self.min_rem_smooth = None
            self._cur_status = status

        out['f_chr_pct'] = 100 * cur_fill / max_fill
        out['f_chr_pct_design'] = 100 * cur_fill / max_fill_d

        if out['b_chr']:
            m_rem = 60 * (max_fill - cur_fill) / drain
        elif out['b_dis']:
            m_rem = int(60 * cur_fill / drain)
        else:
            m_rem = -1  # distinct from None

        out['i_min_rem'] = m_rem
        if self.min_rem_smooth is None:
            self.min_rem_smooth = m_rem
        else:
            self.min_rem_smooth =\
                self._p * m_rem + self._q * self.min_rem_smooth
        out['i_min_rem_smooth'] = int(self.min_rem_smooth)

        return out

    def format(self, output):
        e_prefix = 'bat [{}]'
        if output.pop('b_error_no_bat', False):
            return e_prefix.format(
                colorify('battery {} not found'.format(self.bat_id), BASE08))
        elif output.pop('b_error_unknown_format', False):
            return e_prefix.format(
                colorify('battery {} readout in unknown format'
                         .format(self.bat_id), BASE08))

        # if clicked, show a border; if unclicked, clear it
        # if self._clicked:
        #     self.permanent_overrides['border'] = BASE08
        # elif 'border' in self.permanent_overrides:
        #     del self.permanent_overrides['border']

        # if self._clicked, show % of design capacity instead
        # pct = output['f_chr_pct']
        pct = (output['f_chr_pct'] if not self._clicked
               else output['f_chr_pct_design'])
        pct_str = colorify('{:3.0f}'.format(pct), get_color(pct, rev=True))

        status_string = 'unk'
        if output['b_chr']:
            status_string = colorify('chr', BASE0B)
        elif output['b_dis']:
            status_string = colorify('dis', BASE09)
        elif output['b_full']:
            status_string = 'ful'
        else:
            status_string = 'bal'

        m_rem = output['i_min_rem_smooth']
        if m_rem > 0:
            rem_string = '{:02d}:{:02d}'.format(m_rem // 60, m_rem % 60)
        else:
            rem_string = '--:--'

        braces = '[]' if not self._clicked else ['&lt;', '&gt;']
        return ('bat {}{}%{} [{} rem, {}]'
                .format(braces[0], pct_str, braces[1],
                        rem_string, status_string)
                )


class PY9Wireless(PY9Unit):
    """Provide wireless network information.

    Output API:
        's_SSID': SSID of the connected network
        'f_quality': connection quality, %

    Error API:
        'err_b_down': wireless interface is down
        'err_b_disconnected': connected to network?

    Requires:
        wireless-tools
    """

    def __init__(self, wlan_if, *args, **kwargs):
        """
        Args:
            wlan_id:
                wireless interface name
        """
        self.wlan_if = wlan_if
        super().__init__(*args, requires=['iwconfig'], **kwargs)

    def read(self):
        # Future: read stats from /proc/net/wireless?
        # Raw
        out = check_output(['iwconfig', self.wlan_if]).decode('ascii')
        # line1 = out.split('\n')[0]

        # Status
        # No device detected case
        if 'No such device' in out:  # if not connected: 'No such device'
            return {'err_b_down': True}
        # Not connected case

        if 'off/any' in out:  # if not connected: 'ESSID:off/any'
            return {'err_b_disconnected': True}

        # Raw output data
        raw_SSID = findall('ESSID:"(.*?)"', out)[0]

        n, d = findall('Link Quality=(\d+)/(\d+)', out)[0]
        quality = 100 * float(n) / float(d)

        return {'s_SSID': raw_SSID, 'f_quality': quality}

    def format(self, output):
        prefix = "wlan {} [".format(self.wlan_if)
        suffix = "]"
        if output.pop('err_b_down', False):
            return prefix + colorify('down', BASE08) + suffix
        elif output.pop('err_b_disconnected', False):
            return prefix + colorify('---', BASE0E) + suffix
        else:
            template = prefix + '{}] [{}%' + suffix
            quality = output['f_quality']
            q_color = get_color(quality, rev=True)
            q_str = colorify('{:3.0f}'.format(quality), q_color)
            return template.format(output['s_SSID'], q_str)


class PY9Net(PY9Unit):
    '''
    monitor bytes sent and received per unit time on a network interface

    Output API:
        'f_Bps_down': download rate in bytes per second
        'f_Bps_up': upload rate in bytes per second

    Error API:
        'b_if_down': true if the interface is accessible but explicitly down
        'b_if_loading': true if the unit hasn't fully initialized
        'b_if_error': true if the interface statistics cannot be read
            for whatever reason
    '''

    def __init__(self, i_f, *args, smooth=5, **kwargs):
        '''
        Args:
            i_f:
                the interface name
            smooth:
                int, number of samples to average over for boxcar filter
        '''

        super().__init__(*args, **kwargs)
        self.i_f = i_f

        self.rx_file = '/sys/class/net/{}/statistics/rx_bytes'.format(i_f)
        self.tx_file = '/sys/class/net/{}/statistics/tx_bytes'.format(i_f)
        self.operfile = '/sys/class/net/{}/operstate'.format(i_f)
        self.mark = None
        self.smooth = smooth
        self._rxtx_dq = deque([None] * smooth, maxlen=smooth)
        self._time_dq = deque([None] * smooth, maxlen=smooth)

    def _get_rx_tx(self):
        with open(self.rx_file, 'r') as f:
            rx = int(f.read())
        with open(self.tx_file, 'r') as f:
            tx = int(f.read())
        return rx, tx

    def read(self):
        try:
            with open(self.operfile, 'r') as f:
                if "down" in f.read():
                    self.mark = None
                    return {'b_if_down': True}
        except FileNotFoundError:
            return {'b_if_error': True}

        rx, tx = self._get_rx_tx()
        self._rxtx_dq.append((rx, tx))
        self._time_dq.append(time.time())

        if self._time_dq[0] is None:
            return {'b_if_loading': True}
        else:
            dt = self._time_dq[-1] - self._time_dq[0]
            rxd = self._rxtx_dq[-1][0] - self._rxtx_dq[0][0]
            txd = self._rxtx_dq[-1][1] - self._rxtx_dq[0][1]

            rxr = rxd/dt
            txr = txd/dt

            return {'f_Bps_down': rxr,
                    'f_Bps_up': txr}

    def format(self, output):
        prefix = 'net {} '.format(self.i_f)

        if (output.pop('b_if_error', False) or
                output.pop('b_if_down', False)):
            return prefix + colorify('down', BASE08)

        if output.pop('b_if_loading', False):
            return prefix + colorify('loading', BASE0E)

        sfs = [colorify('B/s', BASE03), colorify('B/s', BASE03)]
        vals = [output['f_Bps_down'], output['f_Bps_up']]
        for ix in range(2):
            for mag, sf in [(30, colorify('G/s', BASE0E)),
                            (20, colorify('M/s', BASE07)),
                            (10, 'K/s')]:
                if vals[ix] > 1 << mag:
                    vals[ix] /= 1 << mag
                    sfs[ix] = sf
                    break

        return (prefix +
                '[u {:6.1f} {:>3s}] '.format(vals[1], sfs[1]) +
                '[d {:6.1f} {:>3s}] '.format(vals[0], sfs[0]))


class PY9Disk(PY9Unit):
    '''
    monitor disk activity

    Output API:
        'b_read': whether the disk has been read since the last check
        'b_write': whether the disk has been written to since the last check

    Error API:
        'b_no_disk': disk statistics cannot be read for the disk
            (it probably does not exist)
        'b_disk_loading': disk information is loading
    '''

    def __init__(self, disk, *args, bs=512, **kwargs):
        '''
        Args:
            disk:
                the disk label as found in `/dev/`, e.g. "sda", etc.
            bs:
                the disk block size in bytes, will usually be 512
        '''
        self.disk = disk
        self.bs = bs
        self.stat = '/sys/class/block/{}/stat'.format(self.disk)

        super().__init__(*args, **kwargs)

        self.last_r = None
        self.last_w = None

    def read(self):
        # TODO: free space, in flight reading, read magnitudes
        try:
            with open(self.stat, 'r') as f:
                _, _, r, _, _, _, w, _, ifl, _, _ =\
                    [int(x) for x in f.read().split()]
        except FileNotFoundError:
            return

        out = {'b_read': False, 'b_write': False}

        if self.last_r is not None:
            dr = r - self.last_r
            dw = w - self.last_w
            self.last_r = r
            self.last_w = w
            if dr > 0:
                out['b_read'] = True
            if dw > 0:
                out['b_write'] = True
        else:
            self.last_r = r
            self.last_w = w
            return {'b_loading': True}

        return out

    def format(self, output):
        context = 'disk [' + self.disk + ' {}]'
        if output.pop('b_no_disk', False):
            return context.format(colorify('---', BASE08))
        if output.pop('b_loading', False):
            return context.format(colorify('loading', BASE0E))

        r_fmt = {'color': BASE00}
        if output['b_read']:
            r_fmt['background'] = BASE0D

        w_fmt = {'color': BASE00}
        if output['b_write']:
            w_fmt['background'] = BASE09

        return context.format('{}{}'.format(pangofy('R', **r_fmt),
                                            pangofy('W', **w_fmt)))
