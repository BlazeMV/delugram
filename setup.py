from setuptools import find_packages, setup
import subprocess
import re

def get_git_version():
    try:
        # Get latest tag + commit count (e.g., "v0.2.1-3-gabc1234")
        git_version = subprocess.check_output(
            ["git", "describe", "--tags", "--match", "v*"], stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()

        # Extract SemVer (e.g., "v0.2.1" from "v0.2.1-3-gabc1234")
        match = re.match(r"v(\d+\.\d+\.\d+)(?:-(\d+)-g[0-9a-f]+)?", git_version)
        if match:
            base_version = match.group(1)  # "0.2.1"
            commit_count = int(match.group(2) or 0)  # "3" (or 0 if no extra commits)

            # Split into Major.Minor.Patch
            major, minor, patch = map(int, base_version.split("."))

            # Increment patch version if there are new commits
            if commit_count > 0:
                patch += commit_count

            return f"{major}.{minor}.{patch}"

    except subprocess.CalledProcessError:
        return "0.0.1"  # Default version if no tags exist

    return "0.0.1"  # Fallback version


__plugin_name__ = 'Delugram'
__author__ = 'BlazeMv'
__author_email__ = 'ad.adamdavid72@gmail.com'
__version__ = get_git_version()
__url__ = 'https://github.com/BlazeMV/delugram'
__license__ = 'MIT'
__description__ = 'Deluge telegram plugin'
__long_description__ = """Deluge plugin to integrate Telegram with your Deluge Server"""
__pkg_data__ = {__plugin_name__.lower(): ['data/*']}

setup(
    name=__plugin_name__,
    version=__version__,
    description=__description__,
    author=__author__,
    author_email=__author_email__,
    url=__url__,
    license=__license__,
    long_description=__long_description__,

    packages=find_packages(),
    package_data=__pkg_data__,

    entry_points="""
    [deluge.plugin.core]
    %s = %s:CorePlugin
    [deluge.plugin.gtk3ui]
    %s = %s:Gtk3UIPlugin
    [deluge.plugin.web]
    %s = %s:WebUIPlugin
    [delugram.libpaths]
    vendor = delugram.vendor
    """ % ((__plugin_name__, __plugin_name__.lower()) * 3)
)
