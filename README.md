# py3status
## an i3bar status line in python 3.

A simple alternative to i3status or i3blocks, this program's principle is to have each element of the status line be a python class with a basic interface, polled by a controller class.

The emphasis is on **total user control** and **code transparency**, followed in time with a performance tightening. This application is geared toward those *willing and able to program python* to configure it. This is much in the spirit of [XMonad](http://xmonad.org/), except with fewer catamorphisms.

### writing a new unit

To add a new unit, derive the PY3Unit class and override the three core methods:
- `read`
- `format`
and, optionally,
- `handle_click`

`read` takes no argument and produces either a dict of output values. It is recommended to document this well following the convention in the example units. Output what you please, and these values will be processed by the format method.

`format` takes the dict output by `read` and outputs a string, with pango formatting, to be displayed on the statusline. For well written units, the documentation should document read's output, and format should be overwritable without delving into `read`'s inner workings. This method is how output is customized - you're in charge!

`handle_click` takes a dict corresponding to a click event (according to the i3bar JSON API) and returns nothing. This code should a) produce useful side effects independent of the py3status control loop and/or b) modify the unit's state such that the next invocation of `get_chunk` does something usefully different. Really, do whatever.

### controlling display style

In addition to in-string display styles and colours, which are controlled by `unit.format`, global styles conforming to the i3bar JSON API can be set in three ways, in *increasing order of precedence*:

- 1) at the instantiation of the PY3Status controller, passing a style dict to the `chunk_kwargs` kwarg. This will be applied to all units.
- 2) on a per-unit basis through the `unit.permanent_overrides` dict. When the unit's output is read, the style will be updated with these parameters.
- 3) on a per-unit basis through the `unit.transient_overrides` dict. Each time the unit's output is read, the style will be updated with these parameters, *and this dictionary will be cleared by the control loop*.

## Installation

- 1) put `py3status.py` and `py3_default_units.py` in your PYTHONPATH
- 2) put `run_py3s.py` in your PATH and configure i3 to run it with `status_command run_py3s.py` in your config
- 3) edit `run_py3s.py` to your heart's content to configure the output. Write your own units, or butcher the defaults.
- !4) There should be no need to edit the control loop, however; that's the one piece of work that's done for you.

## The Future

A migration to cython is possible.
