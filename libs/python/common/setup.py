from pathlib import Path
from runpy import run_path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        # Also stamp wheels built by older native installers during their first update.
        version = run_path("src/drastic_common/version.py")["get_version"]()
        (Path(self.build_lib) / "drastic_common/build-version.txt").write_text(version + "\n")


setup(cmdclass={"build_py": BuildPy})
