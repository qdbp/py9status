import sys

from .py9status import py9core
from .py9status import py9s_default_units

# TODO: I have no idea how hacky this is
# but there must surely be a more idiomatic way

# without this, "from py9status import py9core" works,
# but "from py9status.py9core import X" fails because the true module name is
# still "py9status.src.py9core"

# in either case, this doesn't look like a particularly dangerous operation
sys.modules['py9status.py9core'] =\
        sys.modules['py9status.py9status.py9core']
sys.modules['py9status.py9s_default_units'] =\
        sys.modules['py9status.py9status.py9s_default_units']
