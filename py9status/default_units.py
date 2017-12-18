import time
from atexit import register as exreg
from collections import deque
from datetime import datetime as dtt
from glob import glob
from statistics import mean
from re import findall
from subprocess import check_output
from typing import Deque as Deque_t

from .core import (BASE00, BASE0A, BASE0B, BASE0C, BASE0D, BASE0E,  # noqa \
                   BASE0F, BASE01, BASE02, BASE03, BASE04, BASE05, BASE06,
                   BASE07, BASE08, BASE09, PY9Unit, colorify, get_color,
                   maybe_int, mk_tcolor_str, pangofy)


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

    Error API:
        'b_err_notemp': True if temperature is not available
    '''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, requires=['mpstat'], **kwargs)
        self.pstat = open('/proc/stat', 'r')
        exreg(self.pstat.close)

        self.tt0, self.tu0, self.tk0 = self._read_cpu_times()
        self.show_breakdown = False
        # to make sure that cpuinfo is updated at least once on read
        time.sleep(0.01)

    @property
    def api(self):
        return {
            'p_u': (float, 'Fraction of cpu time used by userland.'),
            'p_k': (float, 'Fraction of cpu time used by kernel.'),
            'temp_C': (float, 'Average cpu temperature.'),
        }

    def _read_cpu_times(self):
        self.pstat.seek(0)

        comps = [int(x) for x in self.pstat.readline().split(' ')[1:] if x]

        return sum(comps), comps[0] + comps[1], comps[2]

    def read(self):
        # TODO: implement smoothing

        tt, tu, tk = self._read_cpu_times()
        dtt = tt - self.tt0
        dtu = tu - self.tu0
        dtk = tk - self.tk0
        self.tt0, self.tu0, self.tk0 = tt, tu, tk

        p_u = dtu / dtt
        p_k = dtk / dtt

        temp = 0.
        n_cores = 0
        # assume this exists
        # XXX modernize
        for fn in glob('/sys/class/thermal/thermal_zone*/temp'):
            with open(fn, 'r') as f:
                try:
                    temp += float(f.read()) / 1000
                    n_cores += 1
                except Exception:
                    temp = None  # type: ignore

        if temp is not None:
            temp /= n_cores

        out = {'p_k': p_k, 'p_u': p_u}
        out.update({'temp_C': temp}
                   if temp is not None else {'b_err_notemp': True})

        return out

    def format(self, output):
        pu = output['p_u'] * 100
        pk = output['p_k'] * 100

        no_temp = output.pop('b_err_notemp', False)

        if no_temp:
            tcolor_str = colorify('unk', BASE09)
        else:
            temp = output['temp_C']
            tcolor_str = mk_tcolor_str(temp)

        if self.show_breakdown:
            load_str = 'u ' + colorify(f'{pu:3.0f}', get_color(pu)) + \
                '% k' + colorify(f'{pk:3.0f}', get_color(pk)) + '%'
        else:
            load_str =\
                'load ' + colorify(f'{pu + pk:3.0f}%', get_color(pu + pk))

        return 'cpu [' + load_str + '] [temp ' + tcolor_str + 'C]'

    def handle_click(self, *args):
        self.show_breakdown = not self.show_breakdown


class PY9Mem(PY9Unit):
    '''
    monitor memory usage

    Requires:
        free

    Output API:
        'f_used_G':  used memory, gigabytes
        'i_used_pct': used memory, %
    '''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.f_mem = open('/proc/meminfo', 'r')
        exreg(self.f_mem.close)

    @property
    def api(self):
        # FIXME add detailed onclick info for buffers, etc
        return {
            'used_GiB': (float, 'Used memory, GiB'),
            'used_frac': (float, 'Fraction of memory used'),
        }

    def read(self):

        self.f_mem.seek(0)
        memlines = self.f_mem.readlines()
        m_tot_g = int(memlines[0].split(' ')[-2]) >> 20
        m_av_g = int(memlines[2].split(' ')[-2]) >> 20

        used_g = m_tot_g - m_av_g
        used_p = used_g / m_tot_g

        return {
            'used_GiB': used_g,
            'used_frac': used_p,
        }

    def format(self, output):
        mp = output['used_frac'] * 100
        ug = output['used_GiB']

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

    STATUS_DIS = 0
    STATUS_CHR = 1
    STATUS_BAL = 2
    STATUS_FUL = 3
    STATUS_UNK = 4

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

        self._clicked = False
        self._cur_status = None

        self.P_hist: Deque_t[float] = deque([], maxlen=int(10 / self.ival))

        self.f_uevent = open(f'/sys/class/power_supply/BAT{bat_id}/uevent')
        exreg(self.f_uevent.close)

    def _parse_uevent(self):
        self.f_uevent.seek(0)
        # 13 = len('POWER_SUPPLY_')
        out = {}

        for line in self.f_uevent.readlines():
            k, v = line.strip().split('=')
            out[k[13:].lower()] = maybe_int(v.lower())

        return out

    @property
    def api(self):
        return {
            'err_no_bat':
                (bool, 'The battery corresponding to the given battery id was '
                       'not found on the system.'),
            'err_bad_format':
                (bool, '$bat/uevent had an unrecognized format.'),
            'status':
                (int, 'The current battery status, as given by the STATUS_*'
                      'class variables'),
            'sec_rem':
                (float, 'Seconds remaning until empty/full. -1 if indefinite, '
                        'None if loading.'),
            'charged_f':
                (float, 'Fraction charged of current max capacity.'),
            'charged_f_design':
                (float, 'Fraction charged of factory capacity.'),
        }

    def read(self):

        # micro-X-hour to SI
        uhtosi = 0.0036

        self.called += 1
        ued = self._parse_uevent()

        if not ued['present']:
            return {'err_no_bat': True}

        # all units SI
        try:
            # if we have only charges and currents, do the power
            # calculations ourselves.
            if 'charge_now' in ued:
                # given in uAh, convert to C
                Q = uhtosi * ued['charge_now']
                Qmx = uhtosi * ued['charge_full']
                Qmxd = uhtosi * ued['charge_full_design']

                V = ued['voltage_now'] / 1e6
                Vmn = ued['voltage_min_design'] / 1e6

                I = ued['current_now'] / 1e6
                P = I * V

                # assume V(Q) is linear, V(0) = Vmin
                # then Vmxd = Vmin + (Qmxd / Q) * (V - Vmin)
                # then Emxd = Qmx * (V0 + Vmx) / 2

                # NOTE could build a more sophisticated statistical model for
                # V(Q), but that seems needlessly complicated

                Vmx = V * (Qmx / Q)
                Emx = Qmx * (Vmn + Vmx) / 2

                Vmxd = V * (Qmxd / Q)
                Emxd = Qmxd * (Vmn + Vmxd) / 2

                # E(Q), integrating the assumed linear relationship
                E = Q * (Vmn + Q * (Vmxd - Vmn) / (2 * Qmxd))

            else:
                # XXX test these on different models
                P = ued['power_now'] / 1e6
                # these are given in watt hours, SIfy them
                E = uhtosi * ued['energy_now']
                Emx = uhtosi * ued['energy_full']
                Emxd = uhtosi * ued['energy_full_design']

            charged_f = E / Emx
            charged_f_design = E / Emxd

        except KeyError:
            return {'err_bad_format': True}

        out = {'charged_f': charged_f, 'charged_f_design': charged_f_design}

        raw_status = ued['status']

        if raw_status == "charging":
            status = self.STATUS_CHR
        elif raw_status == "full":
            status = self.STATUS_FUL
        elif raw_status == 'unknown':
            status = self.STATUS_UNK
        elif raw_status == 'discharging':
            status = self.STATUS_DIS
        elif P == 0:
            status = self.STATUS_BAL
        else:
            status = self.STATUS_UNK

        out['status'] = status

        # reset the smoothing if we detect a status change
        if status != self._cur_status:
            self.min_rem_smooth = None
            self._cur_status = status
            self.P_hist.clear()

        self.P_hist.append(P)

        if len(self.P_hist) < 10:
            return out

        av_p = mean(self.P_hist)

        if status == self.STATUS_CHR:
            sec_rem = (Emx - E) / av_p
        elif status == self.STATUS_DIS:
            sec_rem = E / av_p
        else:
            sec_rem = -1

        out['sec_rem'] = sec_rem

        return out

    def format(self, info):
        e_prefix = f'bat{self.bat_id}' + ' [{}]'

        if info.pop('err_no_bat', False):
            return e_prefix.format(colorify('no bat', BASE08))

        elif info.pop('err_bad_format', False):
            return e_prefix.format(colorify('loading', BASE09))

        # if self._clicked, show % of design capacity instead
        # pct = output['f_chr_pct']

        if self._clicked:
            pct = 100 * info['charged_f_design']
        else:
            pct = 100 * info['charged_f']

        pct_str = colorify(f'{pct:3.0f}', get_color(pct, rev=True))

        st = info['status']

        if st == self.STATUS_CHR:
            st_string = colorify('chr', BASE0B)
        elif st == self.STATUS_DIS:
            st_string = colorify('dis', BASE09)
        elif st == self.STATUS_FUL:
            st_string = colorify('ful', BASE0D)
        elif st == self.STATUS_BAL:
            st_string = colorify('bal', BASE0C)
        else:
            st_string = colorify('unk', BASE0E)

        raw_sec_rem = info.get('sec_rem')

        if raw_sec_rem is None:
            rem_string = colorify('loading', BASE0E)
        elif raw_sec_rem < 0:
            rem_string = '--:--'
        else:
            isr = round(raw_sec_rem)
            min_rem = (isr // 60) % 60
            hr_rem = isr // 3600

            rem_string = f'{hr_rem:02d}:{min_rem:02d}'

        x = '[]' if not self._clicked else ['&lt;', '&gt;']

        return f'bat {x[0]}{pct_str}%{x[1]} [{rem_string} rem, {st_string}]'

    def handle_click(self, click):
        self._clicked = not self._clicked


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
    Monitor bytes sent and received per unit time on a network interface.
    '''

    def __init__(self, i_f, *args, smooth=10, **kwargs):
        '''
        Args:
            i_f:
                the interface name
            smooth:
                int, number of samples to average over for boxcar filter
        '''

        super().__init__(*args, **kwargs)
        self.i_f = i_f

        # FIXME we can in principle keep these files open, we just need
        # special exception handing for cases when the interface goes down
        # and then back up
        self.rx_file = f'/sys/class/net/{i_f}/statistics/rx_bytes'
        self.tx_file = f'/sys/class/net/{i_f}/statistics/tx_bytes'
        self.operfile = f'/sys/class/net/{i_f}/operstate'
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

    @property
    def api(self):
        return {
            'err_if_down': (bool, 'The named interface is down.'),
            'err_if_gone': (bool, 'The named interface does not exist.'),
            'err_if_loading':
                (bool, 'Currently loading statistics for the interface'),
            'Bps_down': (float, 'Bytes per second, ingress'),
            'Bps_up': (float, 'Bytes per second, egress'),
        }

    def read(self):
        try:
            with open(self.operfile, 'r') as f:
                if "down" in f.read():
                    self.mark = None
                    return {'err_if_down': True}
        except OSError:
            return {'err_if_gone': True}

        rx, tx = self._get_rx_tx()
        self._rxtx_dq.append((rx, tx))
        self._time_dq.append(time.time())

        if self._time_dq[0] is None:
            return {'err_if_loading': True}
        else:
            dt = self._time_dq[-1] - self._time_dq[0]
            rxd = self._rxtx_dq[-1][0] - self._rxtx_dq[0][0]
            txd = self._rxtx_dq[-1][1] - self._rxtx_dq[0][1]

            rxr = rxd / dt
            txr = txd / dt

            return {
                'Bps_down': rxr,
                'Bps_up': txr,
            }

    def format(self, output):
        prefix = f'net {self.i_f} '

        if output.pop('err_if_gone', False):
            return prefix + colorify('gone', BASE08)
        if output.pop('err_if_down', False):
            return prefix + colorify('down', BASE09)
        if output.pop('err_if_loading', False):
            return prefix + colorify('loading', BASE0E)

        sfs = [colorify('B/s', BASE03), colorify('B/s', BASE03)]
        vals = [output['Bps_down'], output['Bps_up']]
        for ix in range(2):
            for mag, sf in [(30, colorify('G/s', BASE0E)),
                            (20, colorify('M/s', BASE07)),
                            (10, 'K/s')]:
                if vals[ix] > 1 << mag:
                    vals[ix] /= 1 << mag
                    sfs[ix] = sf
                    break

        return (
            prefix +
            f'[u {vals[1]:6.1f} {sfs[1]:>3s}] ' +
            f'[d {vals[0]:6.1f} {sfs[0]:>3s}]'
        )


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
