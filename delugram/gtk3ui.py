import os

import gi  # isort:skip (Required before Gtk import).

gi.require_version('Gtk', '3.0')

# isort:imports-thirdparty
from gi.repository import Gtk, Gdk

# isort:imports-firstparty
import deluge.component as component
from deluge.plugins.pluginbase import Gtk3PluginBase
from deluge.ui.client import client
from deluge.ui.gtk3 import dialogs

from .common import get_resource
from delugram.logger import log

class ErrorDialog(Gtk.Dialog):

    def __init__(self, title, message):
        Gtk.Dialog.__init__(self, title, None, 0, (Gtk.STOCK_OK, Gtk.ResponseType.OK))
        self.message = message
        label = Gtk.Label(label=message)
        self.get_content_area().add(label)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_gravity(Gdk.Gravity.CENTER)
        self.set_border_width(5)
        self.set_default_size(-1, -1)

    def show(self):
        def dialog_response_cb(dialog, response_id):
            dialog.destroy()

        self.set_modal(True)
        self.connect('response', dialog_response_cb)
        self.show_all()

class AddChatDialog(Gtk.Dialog):
    def __init__(self, parent):
        self.parent = parent

    def show(self):
        self.builder = Gtk.Builder()
        self.builder.add_from_file(get_resource('new_chat.ui'))
        self.builder.connect_signals(
            {
                'on_opts_add': self.on_add,
                'on_opts_cancel': self.on_cancel,
                'on_add_chat_dialog_close': self.on_cancel,
            }
        )
        self.dialog = self.builder.get_object('add_chat_dialog')
        self.dialog.set_transient_for(component.get('Preferences').pref_dialog)
        self.builder.get_object('opts_add_button').show()

        self.dialog.run()

    def on_error_show(self, result):
        d = dialogs.ErrorDialog('Error', result.value.message, self.dialog)
        result.cleanFailure()
        d.run()

    def on_added(self, result):
        self.parent.reload_config()
        self.dialog.destroy()

    def on_add(self, event=None):
        try:
            chat_id = self.builder.get_object('input_chat_id').get_text()
            name = self.builder.get_object('input_name').get_text()
            client.delugram.add_chat(chat_id, name).addCallbacks(self.on_added, self.parent.on_error_show)
        except Exception as ex:
            dialogs.ErrorDialog('Incompatible Option', str(ex), self.dialog).run()

    def on_cancel(self, event=None):
        self.dialog.destroy()

class Gtk3UI(Gtk3PluginBase):
    def enable(self):
        self.builder = Gtk.Builder()
        self.builder.add_from_file(get_resource('config.ui'))
        self.builder.connect_signals(self)
        self.add_chat_dialog = AddChatDialog(self)

        component.get('PluginManager').register_hook(
            'on_apply_prefs', self.on_apply_prefs
        )
        component.get('PluginManager').register_hook(
            'on_show_prefs', self.on_show_prefs
        )
        client.register_event_handler(
            'DelugramPollingStatusChangedEvent', self.on_polling_status_changed_event
        )

        self.config = {}

        vbox = self.builder.get_object('chats_vbox')
        sw = Gtk.ScrolledWindow()
        sw.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        vbox.pack_start(sw, True, True, 0)

        self.store = self.create_model()

        self.treeView = Gtk.TreeView(self.store)
        self.treeView.connect('cursor-changed', self.on_listitem_activated)

        self.create_columns(self.treeView)
        sw.add(self.treeView)
        sw.show_all()
        component.get('Preferences').add_page(
            'Delugram', self.builder.get_object('prefs_box')
        )

    def disable(self):
        component.get('Preferences').remove_page('Delugram')
        component.get('PluginManager').deregister_hook(
            'on_apply_prefs', self.on_apply_prefs
        )
        component.get('PluginManager').deregister_hook(
            'on_show_prefs', self.on_show_prefs
        )

    def create_model(self):
        store = Gtk.ListStore(str, str)
        for chat in self.config.get('chats', []):
            store.append(
                [
                    chat['chat_id'],
                    chat['name'],
                ]
            )
        return store

    def create_columns(self, treeview):
        renderertext = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn('Chat ID', renderertext, text=0)
        column.set_sort_column_id(0)
        column.set_min_width(150)
        treeview.append_column(column)

        renderertext = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn('Name', renderertext, text=1)
        column.set_sort_column_id(1)
        treeview.append_column(column)

    def on_add_button_clicked(self, event=None):
        # display options_window
        self.add_chat_dialog.show()

    def on_remove_button_clicked(self, event=None):
        tree, tree_id = self.treeView.get_selection().get_selected()
        chat_id = self.store.get_value(tree_id, 0)
        if chat_id:
            client.delugram.remove_chat(chat_id).addCallbacks(self.reload_config, self.on_error_show)

    def on_restart_button_clicked(self, event=None):
        def cb_restart():
            self.builder.get_object('restart_button').set_sensitive(True)
            self.reload_config()
            d = dialogs.ErrorDialog('Success', 'Delugram has been restarted')
            d.run()

        def restart(result):
            client.delugram.reload_telegram().addCallbacks(
                self.reload_config,
                self.on_error_show,
                callbackArgs=(cb_restart,)
            )

        self.builder.get_object('restart_button').set_sensitive(False)
        self.on_apply_prefs(callback=restart)

    def on_listitem_activated(self, treeview):
        tree, tree_id = self.treeView.get_selection().get_selected()
        if tree_id:
            self.builder.get_object('remove_button').set_sensitive(True)
        else:
            self.builder.get_object('remove_button').set_sensitive(False)

    def on_error_show(self, result):
        d = dialogs.ErrorDialog('Error', result.value.message)
        result.cleanFailure()
        d.run()

    def on_apply_prefs(self, callback=None):
        log.debug('applying prefs for Delugram')

        if callback is None:
            callback = self.reload_config
            self.builder.get_object('restart_button').set_sensitive(True)

        client.delugram.set_config({
            'telegram_token': self.builder.get_object('input_telegram_token').get_text(),
            'admin_chat_id': self.builder.get_object('input_admin_chat_id').get_text(),
        }).addCallbacks(callback, self.on_error_show)

    def on_show_prefs(self):
        self.reload_config()

    def on_polling_status_changed_event(self):
        self.reload_config()

    def reload_config(self, result=None, callback=None):
        client.delugram.get_config().addCallbacks(self.cb_get_config, self.on_error_show, callbackArgs=(callback,))

    def cb_get_config(self, config, callback=None):
        """callback for on show_prefs"""
        log.trace('Got delugram config from core: %s', config)
        self.config = config or {}

        # set ui input_telegram_token and input_admin_chat_id
        self.builder.get_object('input_telegram_token').set_text(self.config.get('telegram_token', ''))
        self.builder.get_object('input_admin_chat_id').set_text(self.config.get('admin_chat_id', ''))
        self.builder.get_object('polling_status_label').set_text(
            'Running ✅' if self.config.get('polling', False)
            else 'Stopped 🚫 (Double check Telegram Token and Restart Polling)')

        # refresh registered chats list
        self.store.clear()
        for chat in self.config.get('chats', []):
            self.store.append(
                [
                    chat['chat_id'],
                    chat['name'],
                ]
            )

        if callback:
            callback()

        # Workaround for cached glade signal appearing when re-enabling plugin in same session
        if self.builder.get_object('remove_button'):
            # Disable the remove button, because nothing in the store is selected
            self.builder.get_object('remove_button').set_sensitive(False)
