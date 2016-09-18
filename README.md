# py3status
## an i3bar status line in python 3.

A simple alternative to i3status or i3blocks, this program's principle is to have each element of the status line be a python class with a basic interface, polled by a controller class.

The emphasis is on **total user control** and **code transparency**, followed in time with a performance tightening. This application is geared toward those *willing and able to program python* to configure it. This is much in the spirit of [XMonad](http://xmonad.org/), except with fewer catamorphisms.

To add a new unit, derive the PY3Unit class and override the two core methods:
- `get_chunk`
- `handle_click`

`get_chunk` takes no argument and produces either a string (to be displayed, with pango formatting), or dict (conforming to the i3bar JSON API) output. Do with this as you please, and whatever you produce will show up on the status line!. The only caveat is that since it's run in a separate thread, and not process, GIL-hogging code will slow down other units.

`handle_click` takes a dict corresponding to a click event (according to the i3bar JSON API) and returns nothing. This code should a) produce useful side effects independent of the py3status control loop and/or b) modify the unit's state such that the next invocation of `get_chunk` does something usefully different. Really, do whatever.

## Installation

- 1) put `py3status.py` and `py3_default_units.py` in your PYTHONPATH
- 2) put `run_py3s.py` in your PATH and configure i3 to run it with `status_command run_py3s.py` in your config
- 3) edit `run_py3s.py` to your heart's content to configure the output. Write your own units, or butcher the defaults.
- !4) There should be no need to edit the control loop, however; that's the one piece of work that's done for you.

## The Future

A migration to cython is possible.
