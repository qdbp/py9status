from setuptools import setup


# semver with automatic minor bumps keyed to unix time
__version__ = '1.3.1511415987'


setup(
    name='py9status',
    version=__version__,
    packages=['py9status'],
    scripts=['py9status/run_py9s.py'],
    # install_requires=['click'],
)
