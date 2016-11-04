# py9status
## an i3bar status line in python 3.

A minimalistic alternative to i3status, i3blocks or py3status. Each element of the status line be a python class with a basic interface, polled by a controller class. Configuration is done by editing the entry point python code directly (and intuitively). This is much in the spirit of [XMonad](http://xmonad.org/), except with fewer catamorphisms.

The emphasis is on **total user control** and **code transparency**, followed in time with a performance tightening. This application is geared toward those *willing and able to program python* to configure it.

### configuring dispay style

To override the display style of a unit, write a class inheriting from it and override the `format` method. Details below.

A good place to do this would be in your local `run_py9s.py` file.

### writing a new unit

To add a new unit, derive the PY9Unit class and override the three core methods:
- `read`
- `format`
and, optionally,
- `handle_click`

`read` takes no argument and produces a dict of output values. This dict will be processed by the `format` method. It is recommended to document the output well, following the convention in the example units.

`format` takes the dict output by `read` and outputs a string, with pango formatting, to be displayed on the statusline. For well written units, the documentation should document `read`'s output, and `format` should be overwritable without delving into `read`'s inner workings.

`handle_click` is called when the unit's output block is clicked on the status line. `handle_click` takes a dict corresponding to a click event (according to the i3bar JSON API) and returns nothing. Use your imagination.

#### controlling display style

In addition to in-string display styles and colours, which are controlled by `unit.format`, global styles conforming to the i3bar JSON API can be set in three ways, in *increasing order of precedence*:

- 1) at the instantiation of the `PY9Status` controller in `run_py9s.py` by passing an i3 API-conformant style dict to the `chunk_kwargs` kwarg. This will be applied to all units.
- 2) on a per-unit basis through the `unit.permanent_overrides` dict. When the unit's output is read, the style will be updated with these parameters.
- 3) on a per-unit basis through the `unit.transient_overrides` dict. Each time the unit's output is read, the style will be updated with these parameters, *and this dictionary will be cleared by the control loop*.

## Installation

- 1) put `py9core.py` and `py9s_default_units.py` in your PYTHONPATH
- 2) put a copy of `run_py9s.py` in your PATH and configure i3 to run it with `status_command run_py9s.py` in your config. For the default units to work out of the box, enable pango formatting.
- 3) edit `run_py9s.py` to configure the output. Write your own units, or butcher the defaults. This file's format remains stable, and updates to py9status should not necessiate changes.
