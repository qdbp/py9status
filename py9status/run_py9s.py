#! /usr/bin/python
from typing import NoReturn

from py9status.core import PY9Status
from py9status.default_units import *


# you can write your own units! it's recommended to use a separate
# file, which you then import as:
#
# >>> from custom_units import *

# units appear on the bar listed order

# feel free to implement more complicated loading logic!


def main() -> NoReturn:
    # common unit kwargs:
    # ival= the target interval between unit updates
    # name= the unit name reported to i3bar
    #       defaults to the class name
    # !!! avoid name conflicts !!!
    units = [
        # uncomment if you have an nvidia GPU
        # PY9NVGPU(ival=5.),
        PY9Mem(ival=3.0),
        PY9CPU(),
        # PY9Net("e0", name="ethernet"),
        # uncomment for laptop users
        # PY9Wireless("wlan_id", ival=5.),
        # PY9Bat(ival=5.),
        PY9Time(ival=0.7),
        # PY9Wireless("wlan0"),
    ]

    py9s = PY9Status(units)
    py9s.run()


if __name__ == "__main__":
    main()
