import sys

from .py3status import py3core
from .py3status import py3s_default_units

# TODO: I have no idea how hacky this is
# but there must surely be a more idiomatic way

# without this, "from py3status import py3core" works,
# but "from py3status.py3core import X" fails because the true module name is
# still "py3status.src.py3core"

# in either case, this doesn't look like a particularly dangerous operation
sys.modules['py3status.py3core'] =\
        sys.modules['py3status.py3status.py3core']
sys.modules['py3status.py3s_default_units'] =\
        sys.modules['py3status.py3status.py3s_default_units']
