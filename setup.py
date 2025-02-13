from setuptools import find_packages, setup

__plugin_name__ = 'Delugram'
__author__ = 'BlazeMv'
__author_email__ = 'ad.adamdavid72@gmail.com'
__version__ = '0.1'
__url__ = 'https://github.com/BlazeMV/delugram'
__license__ = 'MIT'
__description__ = 'Deluge plugin to integrate Telegram with your Deluge Server'
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
    include = delugram.include
    """ % ((__plugin_name__, __plugin_name__.lower()) * 3)
)
