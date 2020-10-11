from setuptools import find_packages, setup


# semver with automatic minor bumps keyed to unix time
__version__ = "2.0.1521582170"


setup(
    name="py9status",
    version=__version__,
    packages=find_packages(),
    scripts=["py9status/run_py9s.py"],
)
