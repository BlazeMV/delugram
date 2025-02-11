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
        log.debug('Applying preferences for Delugram')

        config = {
            'telegram_token': self.builder.get_object('entry_telegram_token').get_text(),
            'admin_chat_id': int(self.builder.get_object('entry_admin_chat_id').get_text()),
            'users': self.get_users_list()
        }

        client.delugram.set_config(config)

    def on_show_prefs(self):
        client.delugram.get_config().addCallback(self.cb_get_config)

    def cb_get_config(self, config):
        """Callback for loading configuration into the UI"""
        self.builder.get_object('entry_telegram_token').set_text(config.get('telegram_token', ''))
        self.builder.get_object('entry_admin_chat_id').set_text(str(config.get('admin_chat_id', '')))

        self.populate_users_list(config.get('users', []))

    def populate_users_list(self, users):
        """Populate the users list in the UI"""
        user_liststore = self.builder.get_object('liststore_users')
        user_liststore.clear()
        for user in users:
            user_liststore.append([user['chat_id'], user['name']])

    def get_users_list(self):
        """Retrieve users from the UI and return them as a list of dictionaries"""
        user_liststore = self.builder.get_object('liststore_users')
        users = []
        for row in user_liststore:
            users.append({"chat_id": row[0], "name": row[1]})
        return users
