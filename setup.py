from setuptools import find_packages, setup

__version__ = "2.3.1"


setup(
    name="py9status",
    version=__version__,
    packages=find_packages(),
    scripts=["py9status/run_py9s.py"],
)
