from deluge.plugins.pluginbase import WebPluginBase
from .common import get_resource


class WebUI(WebPluginBase):
    scripts = [get_resource('delugram.js')]
    debug_scripts = scripts