from setuptools import find_packages, setup


# semver with automatic minor bumps keyed to unix time
__version__ = "2.1.1604711230"


setup(
    name="py9status",
    version=__version__,
    packages=find_packages(),
    scripts=["py9status/run_py9s.py"],
)
