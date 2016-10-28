#! /usr/bin/python

from py3status.py3core import PY3Status
from py3status.py3s_default_units import *

# you can write your own units! it's recommended to use a separate
# file, which you then import as:
#
# >>> from custom_units import *

# units appear on the bar listed order

# feel free to implement more complicated loading logic!


def main():
    # common unit kwargs:
    # ival= the target interval between unit updates
    # name= the unit name reported to i3bar
    #       defaults to the class name
    # !!! avoid name conflicts !!!
    units = [
        # uncomment if you have an nvidia GPU
        # PY3NVGPU(ival=5.),
        PY3Mem(ival=3.),
        PY3CPU(),
        PY3Net('vpn-ca', name='net_vpn'),
        # uncomment for laptop users
        # PY3Bat(ival=5.),
        PY3Time(ival=0.7)
    ]

    py3s = PY3Status(units)
    py3s.run()

if __name__ == '__main__':
    main()
