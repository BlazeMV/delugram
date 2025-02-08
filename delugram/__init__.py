
import sys
from delugram.logger import log

import pkg_resources
from deluge.plugins.init import PluginInitBase


def load_libs():
    egg = pkg_resources.require("Delugram")[0]
    for name in egg.get_entry_map("delugram.libpaths"):
        ep = egg.get_entry_info("delugram.libpaths", name)
        location = "%s/%s" % (egg.location, ep.module_name.replace(".", "/"))
        if location not in sys.path:
            sys.path.append(location)
        log.info("Appending to sys.path: '%s'" % location)


class CorePlugin(PluginInitBase):
    def __init__(self, plugin_name):
        load_libs()
        from .core import Core as PluginClass
        self._plugin_cls = PluginClass
        super(CorePlugin, self).__init__(plugin_name)


class Gtk3UIPlugin(PluginInitBase):
    def __init__(self, plugin_name):
        load_libs()
        from .gtk3ui import Gtk3UI as PluginClass
        self._plugin_cls = PluginClass
        super(Gtk3UIPlugin, self).__init__(plugin_name)


class WebUIPlugin(PluginInitBase):
    def __init__(self, plugin_name):
        load_libs()
        from .webui import WebUI as PluginClass
        self._plugin_cls = PluginClass
        super(WebUIPlugin, self).__init__(plugin_name)
