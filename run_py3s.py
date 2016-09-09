#! /usr/bin/python

from py3status import PY3Status
from py3s_default_units import *

#from custom_units import *

# units load in listed order
# comment-out units to remove them from py3bar

def main():
    units = [
             PY3NVGPU(ival=5.),
             PY3Mem(ival=3.),
             PY3CPU(),
             PY3Net('vpn-ca', name='net_vpn'),
             PY3Time(ival=0.7),
#             PY3Bat(ival=5)
             ]

    py3s = PY3Status(units)
    py3s.run()

if __name__ == '__main__':
    main()
