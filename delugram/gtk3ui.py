from __future__ import unicode_literals

import logging

from gi.repository import Gtk

import deluge.component as component
from deluge.plugins.pluginbase import Gtk3PluginBase
from deluge.ui.client import client

from .common import get_resource

log = logging.getLogger(__name__)


class Gtk3UI(Gtk3PluginBase):
    def enable(self):
        self.builder = Gtk.Builder()
        self.builder.add_from_file(get_resource('config.ui'))

        component.get('Preferences').add_page(
            'delugram', self.builder.get_object('prefs_box'))
        component.get('PluginManager').register_hook(
            'on_apply_prefs', self.on_apply_prefs)
        component.get('PluginManager').register_hook(
            'on_show_prefs', self.on_show_prefs)

    def disable(self):
        component.get('Preferences').remove_page('delugram')
        component.get('PluginManager').deregister_hook(
            'on_apply_prefs', self.on_apply_prefs)
        component.get('PluginManager').deregister_hook(
            'on_show_prefs', self.on_show_prefs)

    def on_apply_prefs(self):
        log.debug('applying prefs for delugram')
        config = {
            'test': self.builder.get_object('txt_test').get_text()
        }
        client.delugram.set_config(config)

    def on_show_prefs(self):
        client.delugram.get_config().addCallback(self.cb_get_config)

    def cb_get_config(self, config):
        """callback for on show_prefs"""
        self.builder.get_object('txt_test').set_text(config['test'])
