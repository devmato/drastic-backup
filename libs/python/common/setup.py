from pathlib import Path
from runpy import run_path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        # Also stamp wheels built by older native installers during their first update.
        metadata = run_path("src/drastic_common/version.py")
        version = metadata["get_version"]()
        package = Path(self.build_lib) / "drastic_common"
        (package / "build-version.txt").write_text(version + "\n")
        revision = metadata["get_revision"]()
        if revision:
            (package / "build-revision.txt").write_text(revision + "\n")
        else:
            (package / "build-revision.txt").unlink(missing_ok=True)


setup(cmdclass={"build_py": BuildPy})
