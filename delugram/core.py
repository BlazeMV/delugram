from __future__ import unicode_literals

import html
import json
import time
import traceback
import urllib
from base64 import b64encode

from delugram.logger import log
import deluge.configmanager
from deluge import component
from deluge.common import fsize, ftime, fdate, fpeer, fpcnt, fspeed, is_magnet, is_url
from deluge.core.rpcserver import export
from deluge.plugins.pluginbase import CorePluginBase
from telegram import (Bot, Update, ParseMode, ReplyKeyboardRemove, ReplyKeyboardMarkup)
from telegram.ext import (Updater, CommandHandler, CallbackContext, ConversationHandler, MessageHandler, Filters,
                          DispatcherHandlerStop)
from telegram.utils.request import Request


DEFAULT_PREFS = {
    "telegram_token": "Contact @BotFather, create a new bot and get a bot token",
    "admin_chat_id": "Telegram chat id of the administrator. Use @userinfobot to get the chat id",
    "chats": [],
    "chat_torrents": {},
}

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
                         '(KHTML, like Gecko) Chrome/74.0.3729.169 Safari/537.36'}

SET_LABEL_STATE, TORRENT_TYPE_STATE, ADD_MAGNET_STATE, ADD_TORRENT_STATE, ADD_URL_STATE = range(5)

EMOJI = {'seeding':     '\u23eb',
         'queued':      '\u23ef',
         'paused':      '\u23f8',
         'error':       '\u2757\ufe0f',
         'downloading': '\u23ec'}

INFO_DICT = (('queue', lambda i, s: i != -1 and str(i) or '#'),
             ('state', None),
             ('name', lambda i, s: ' %s %s\n*%s* ' %
              (s['state'] if s['state'].lower() not in EMOJI
               else EMOJI[s['state'].lower()], s['state'],
               i)),
             ('total_wanted', lambda i, s: '(%s) ' % fsize(i)),
             ('progress', lambda i, s: '%s\n' % fpcnt(i/100)),
             ('num_seeds', None),
             ('num_peers', None),
             ('total_seeds', None),
             ('total_peers', lambda i, s: '%s / %s seeds\n' %
              tuple(map(fpeer, (s['num_seeds'], s['num_peers']),
                               (s['total_seeds'], s['total_peers'])))),
             ('download_payload_rate', None),
             ('upload_payload_rate', lambda i, s: '%s : %s\n' %
              tuple(map(fspeed, (s['download_payload_rate'], i)))),
             ('eta', lambda i, s: i > 0 and '*ETA:* %s ' % ftime(i) or ''),
             ('time_added', lambda i, s: '*Added:* %s' % fdate(i)))

INFOS = [i[0] for i in INFO_DICT]


class Core(CorePluginBase):

    def enable(self, throw_polling_exceptions=False):
        self.core = component.get('Core')
        self.config = deluge.configmanager.ConfigManager(
            'delugram.conf', DEFAULT_PREFS)

        # hydrate
        self.commands = [
            {
                'name': 'start',
                'description': 'Start of the conversation',
                'handler': CommandHandler('start', self.help_command_handler),
                'list_in_help': False
            },
            {
                'name': 'add',
                'description': 'Add a new torrent',
                'handler': ConversationHandler(
                    entry_points=[CommandHandler('add', self.add_command_handler)],
                    states={
                        SET_LABEL_STATE: [
                            MessageHandler(Filters.text & ~Filters.command, self.set_label_state_handler),
                            MessageHandler(Filters.all & ~Filters.command, self.invalid_input_handler),
                        ],
                        TORRENT_TYPE_STATE: [
                            MessageHandler(Filters.regex("^Magnet$"), self.torrent_type_state_magnet_handler),
                            MessageHandler(Filters.regex(r"^\.torrent$"), self.torrent_type_state_torrent_handler),
                            MessageHandler(Filters.regex("^URL$"), self.torrent_type_state_url_handler),
                            MessageHandler(Filters.all & ~Filters.command, self.torrent_type_state_unknown_handler),
                        ],
                        ADD_MAGNET_STATE: [
                            MessageHandler(Filters.text & ~Filters.command, self.add_magnet_state_handler),
                            MessageHandler(Filters.all & ~Filters.command, self.invalid_input_handler),
                        ],
                        ADD_TORRENT_STATE: [
                            MessageHandler(Filters.document, self.add_torrent_state_handler),
                            MessageHandler(Filters.all & ~Filters.command, self.invalid_input_handler),
                        ],
                        ADD_URL_STATE: [
                            MessageHandler(Filters.text & ~Filters.command, self.add_url_state_handler),
                            MessageHandler(Filters.all & ~Filters.command, self.invalid_input_handler),
                        ]
                    },
                    fallbacks=[
                        CommandHandler('cancel', self.cancel_command_handler)
                    ],
                    # conversation_timeout=120,
                ),
                'list_in_help': True
            },
            {
                'name': 'status',
                'description': 'Show status of active torrents',
                'handler': CommandHandler('status', self.status_command_handler),
                'list_in_help': True
            },
            {
                'name': 'cancel',
                'description': 'Cancels the current operation',
                'handler': CommandHandler('cancel', self.cancel_command_handler),
                'list_in_help': True
            },
            {
                'name': 'help',
                'description': 'List all available commands',
                'handler': CommandHandler('help', self.help_command_handler),
                'list_in_help': True
            },
            {
                'name': '/register',
                'description': 'Register new chat',
                'handler': CommandHandler('register', self.register_command_handler),
                'list_in_help': False
            },
            {
                'name': '/deregister',
                'description': 'Deregister already registered chat',
                'handler': CommandHandler('deregister', self.deregister_command_handler),
                'list_in_help': False
            }
        ]

        self.torrent_manager = component.get("TorrentManager")
        self.event_manager = component.get("EventManager")
        self.label_plugin = None
        self.available_labels = self.load_available_labels()

        # check if the telegram token is set, if not, no need to go any further
        if self.config['telegram_token'] == DEFAULT_PREFS['telegram_token']:
            return

        # initialize telegram bot
        self.bot = Bot(self.config['telegram_token'], request=Request(con_pool_size=8))
        self.updater = Updater(bot=self.bot, use_context=True)
        self.dispatcher = self.updater.dispatcher

        # register tg middleware
        self.dispatcher.add_handler(MessageHandler(Filters.all, self.tg_middleware), group=0)

        # register command handlers to telegram
        for cmd in self.commands:
            self.dispatcher.add_handler(cmd['handler'], group=1)

        # register error handlers to telegram
        self.dispatcher.add_error_handler(self.tg_on_error)

        # register torrent download finished event listener
        self.event_manager.register_event_handler(
            'TorrentAddedEvent', self._on_torrent_added
        )
        self.event_manager.register_event_handler(
            'TorrentRemovedEvent', self._on_torrent_removed
        )
        self.event_manager.register_event_handler(
            'TorrentFinishedEvent', self._on_torrent_finished
        )

        try:
            log.info("Starting to poll")

            # start polling
            self.updater.start_polling(poll_interval=1, allowed_updates=[Update.MESSAGE])

            log.info("Polling started")

        except Exception as e:
            log.info("Polling failed with token: %s" % self.config['telegram_token'])
            log.error(str(e) + '\n' + traceback.format_exc())
            if throw_polling_exceptions:
                raise Exception(f"Telegram: unable to start polling: {e}") from e

        log.debug('Plugin enabled.')

    def disable(self):
        self.config.save()

        # unregister torrent download finished event listener
        self.event_manager.deregister_event_handler(
            'TorrentAddedEvent', self._on_torrent_added
        )
        self.event_manager.deregister_event_handler(
            'TorrentRemovedEvent', self._on_torrent_removed
        )
        self.event_manager.deregister_event_handler(
            'TorrentFinishedEvent', self._on_torrent_finished
        )

        # stop polling
        if self.updater:
            self.updater.stop()

        log.debug('Plugin disabled')

    def update(self):
        pass


    #########
    #  Section: RPC Methods
    #########

    @export
    def set_config(self, config):
        """Sets the config dictionary"""
        for key in config:
            self.config[key] = config[key]
        self.config.save()

    @export
    def get_config(self):
        """Returns the config dictionary"""
        return self.config.config

    @export
    def add_chat(self, chat_id, name):
        if not chat_id or not name or len(chat_id) < 1 or len(name) < 1:
            raise ValueError("Invalid Chat ID or Name")

        if next((item for item in self.config['chats'] if item["chat_id"] == chat_id), None) is None:
            self.config['chats'].append({"chat_id": chat_id, "name": name})
            self.config.save()
            return True
        return False

    @export
    def remove_chat(self, chat_id):
        self.config['chats'] = [item for item in self.config['chats'] if item["chat_id"] != chat_id]
        self.config.save()
        return True

    @export
    def reload(self, config=None):
        if config and isinstance(config, dict):
            self.set_config(config)

        self.disable()
        self.enable(throw_polling_exceptions=True)


    #########
    #  Section: Event Handlers
    #########

    def _on_torrent_added(self, torrent_id, from_state=False):
        """
        This is called when a torrent is added.
        """

        # clean up existing torrents
        self.cleanup_chat_torrents()

        if from_state:
            return

        torrent = self.torrent_manager[torrent_id]
        if not torrent:
            return

        owner = self.get_torrent_chat(torrent_id)
        if not owner:
            return

        torrent_status = torrent.get_status(['name'])

        log.debug(f'Owner: {owner}, Torrent: {torrent}, Chat torrents: {self.config["chat_torrents"]}')

        message = "Torrent added: *%s*" % html.escape(torrent_status['name'])
        self.bot.send_message(chat_id=owner, text=message, parse_mode=ParseMode.HTML)

    def _on_torrent_removed(self, torrent_id):
        """
        This is called when a torrent is removed.
        """
        self.cleanup_chat_torrents()

    def _on_torrent_finished(self, torrent_id):
        """
        This is called when a torrent is finished.
        """

        torrent = self.torrent_manager[torrent_id]
        if not torrent:
            return

        owner = self.get_torrent_chat(torrent_id)
        if not owner:
            return

        torrent_status = torrent.get_status(['name'])

        message = "Torrent finished: *%s*" % html.escape(torrent_status['name'])
        self.bot.send_message(chat_id=owner, text=message, parse_mode=ParseMode.HTML)

    def tg_on_error(self, update: object, context: CallbackContext) -> None:
        """Log the error and send a telegram message to notify the developer."""
        # Log the error before we do anything else, so we can see it even if something breaks.
        log.error("Exception while handling an update:", exc_info=context.error)

        if not self.config['admin_chat_id']:
            return

        # traceback.format_exception returns the usual python message about an exception, but as a
        # list of strings rather than a single string, so we have to join them together.
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)

        # prep the update, chat_data, and user_data for display
        update_str = update.to_dict() if isinstance(update, Update) else str(update)
        update_str = html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))
        chat_data = html.escape(str(context.chat_data))
        user_data = html.escape(str(context.chat_data))

        # length of the message should not exceed 4096 characters
        if len(tb_string) + len(update_str) + len(chat_data) + len(user_data) > 3800:
            tb_string = html.escape(str(context.error))

            if len(tb_string) + len(update_str) + len(chat_data) + len(user_data) > 3800:
                tb_string = 'See Logs for trace'

        # Build the message with some markup and additional information about what happened.
        message = (
            "An exception was raised while handling an update\n"
            f"<pre>update = {update_str}"
            "</pre>\n\n"
            f"<pre>context.chat_data = {chat_data}</pre>\n\n"
            f"<pre>context.chat_data = {user_data}</pre>\n\n"
            f"<pre>{tb_string}</pre>"
        )

        # Finally, send the message
        context.bot.send_message(
            chat_id=self.config['admin_chat_id'], text=message, parse_mode=ParseMode.HTML
        )

        #notify original chat of the error
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="An error occurred. Administrator has been notified.",
            reply_markup=ReplyKeyboardRemove()
        )

    #########
    #  Section: Telegram Commands
    #########

    def help_command_handler(self, update: Update, context: CallbackContext):
        help_msg = [
            f"/{cmd['name']} - {cmd['description']}" for cmd in self.commands if cmd['list_in_help']
        ]
        update.message.reply_text(
            text='\n'.join(help_msg),
            parse_mode='Markdown',
            # reply_to_message_id=update.message.message_id
        )

    def status_command_handler(self, update: Update, context: CallbackContext):
        chat_torrents = self.config['chat_torrents'].get(str(update.effective_chat.id), [])
        message = self.list_torrents(lambda t:
                               t.get_status(('state',))['state'] in
                               ('Active', 'Downloading', 'Seeding',
                                'Paused', 'Checking', 'Error', 'Queued'
                                ) and str(t.torrent_id) in chat_torrents)

        update.message.reply_text(
            text=message,
            parse_mode='Markdown'
            # reply_to_message_id=update.message.message_id
        )

    def cancel_command_handler(self, update: Update, context: CallbackContext):
        update.message.reply_text(
            text='Operation cancelled',
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()
            # reply_to_message_id=update.message.message_id
        )
        return ConversationHandler.END

    def register_command_handler(self, update: Update, context: CallbackContext):
        if str(update.effective_chat.id) != self.config['admin_chat_id']:
            return

        args = update.message.text.split(sep=' ', maxsplit=3)

        if len(args) != 4:
            update.message.reply_text(
                text="Invalid arguments. Usage: /register <bot_token> <chat_id> <chat_name>"
            )
            return

        if args[1] != self.config['telegram_token']:
            update.message.reply_text(
                text="Invalid bot token. Usage: /register <bot_token> <chat_id> <chat_name>"
            )
            return

        if self.add_chat(chat_id=args[2], name=args[3]):
            update.message.reply_text(
                text="Chat registered successfully\nChat ID: %s\nChat Name: %s" % (args[2], args[3])
            )
        else:
            update.message.reply_text(
                text="Chat ID already registered"
            )

    def deregister_command_handler(self, update: Update, context: CallbackContext):
        if str(update.effective_chat.id) != self.config['admin_chat_id']:
            return

        args = update.message.text.split(sep=' ', maxsplit=2)

        if len(args) != 3:
            update.message.reply_text(
                text="Invalid arguments. Usage: /deregister <bot_token> <chat_id>"
            )
            return

        if args[1] != self.config['telegram_token']:
            update.message.reply_text(
                text="Invalid bot token. Usage: /deregister <bot_token> <chat_id>"
            )
            return

        if self.remove_chat(chat_id=args[2]):
            update.message.reply_text(
                text="Chat deregistered successfully\nChat ID: %s" % (args[2])
            )
        else:
            update.message.reply_text(
                text="Something went wrong"
            )

    def add_command_handler(self, update: Update, context: CallbackContext):
        # refresh available labels list
        self.load_available_labels()

        if len(self.available_labels):
            return self.advance_to_set_label_state(update=update, context=context)

        # if no labels are found, skip the label selection step
        else:
            return self.advance_to_torrent_type_state(update=update, context=context)

    def advance_to_set_label_state(self, update: Update, context: CallbackContext):
        session_msg = context.chat_data.pop('message', '')
        keyboard_options = [[g] for g in self.available_labels]

        update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Select a label",
            reply_markup=ReplyKeyboardMarkup(keyboard_options, one_time_keyboard=True)
            # reply_to_message_id=update.message.message_id
        )
        return SET_LABEL_STATE

    def set_label_state_handler(self, update: Update, context: CallbackContext):
        if update.message.text in self.available_labels:
            context.chat_data['label'] = update.message.text

            return self.advance_to_torrent_type_state(update=update, context=context)
        else:
            context.chat_data['message'] = "Invalid label. Try again"
            return self.advance_to_set_label_state(update=update, context=context)

    def advance_to_torrent_type_state(self, update: Update, context: CallbackContext):
        session_msg = context.chat_data.pop('message', '')
        keyboard_options = [['Magnet'], ['.torrent'], ['URL']]

        update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Select type of torrent source",
            reply_markup=ReplyKeyboardMarkup(keyboard_options, one_time_keyboard=True),
            # reply_to_message_id=update.message.message_id
        )
        return TORRENT_TYPE_STATE

    def torrent_type_state_magnet_handler(self, update: Update, context: CallbackContext):
        return self.advance_to_add_magnet_state(update=update, context=context)

    def advance_to_add_magnet_state(self, update: Update, context: CallbackContext):
        session_msg = context.chat_data.pop('message', '')
        update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Send the magnet link",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADD_MAGNET_STATE

    def torrent_type_state_torrent_handler(self, update: Update, context: CallbackContext):
        return self.advance_to_add_torrent_state(update=update, context=context)

    def advance_to_add_torrent_state(self, update: Update, context: CallbackContext):
        session_msg = context.chat_data.pop('message', '')
        update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Send the torrent file",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADD_TORRENT_STATE

    def torrent_type_state_url_handler(self, update: Update, context: CallbackContext):
        return self.advance_to_add_url_state(update=update, context=context)

    def advance_to_add_url_state(self, update: Update, context: CallbackContext):
        session_msg = context.chat_data.pop('message', '')
        update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Send the torrent url",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADD_URL_STATE

    def torrent_type_state_unknown_handler(self, update: Update, context: CallbackContext):
        context.chat_data['message'] = "Invalid option. Try again"
        return self.advance_to_torrent_type_state(update=update, context=context)

    def add_magnet_state_handler(self, update: Update, context: CallbackContext):
        if not is_magnet(update.message.text):
            context.chat_data['message'] = "Invalid magnet link. Try again"
            return self.advance_to_add_magnet_state(update=update, context=context)

        try:
            tid = self.core.add_torrent_magnet(update.message.text, {})
            self.apply_label(tid=tid, context=context)
            self.add_torrent_for_chat(chat_id=update.effective_chat.id, torrent_id=str(tid))
            return ConversationHandler.END

        except Exception as e:
            update.message.reply_text(
                text="Failed to add magnet link",
                reply_markup=ReplyKeyboardRemove()
            )
            log.error(str(e) + '\n' + traceback.format_exc())

        return ConversationHandler.END

    def add_torrent_state_handler(self, update: Update, context: CallbackContext):
        if update.message.document.mime_type != 'application/x-bittorrent':
            context.chat_data['message'] = "Invalid torrent file. Try again"
            return self.advance_to_add_torrent_state(update=update, context=context)

        try:
            # Grab file & add torrent with label
            file_info = self.bot.getFile(update.message.document.file_id)
            request = urllib.request.Request(file_info.file_path, headers=HEADERS)
            status_code = urllib.request.urlopen(request).getcode()
            if status_code == 200:
                file_contents = urllib.request.urlopen(request).read()
                tid = self.core.add_torrent_file(None, b64encode(file_contents), {})
                self.apply_label(tid, context)
                self.add_torrent_for_chat(chat_id=update.effective_chat.id, torrent_id=str(tid))
                return ConversationHandler.END

            else:
                update.message.reply_text(
                    text="Failed to download torrent file. terminating operation",
                    reply_markup=ReplyKeyboardRemove()
                )
        except Exception as e:
            update.message.reply_text(
                text="Failed to download torrent file. terminating operation",
                reply_markup=ReplyKeyboardRemove()
            )
            log.error(str(e) + '\n' + traceback.format_exc())

        return ConversationHandler.END

    def add_url_state_handler(self, update: Update, context: CallbackContext):
        if not is_url(update.message.text):
            context.chat_data['message'] = "Invalid URL. Try again"
            return self.advance_to_add_url_state(update=update, context=context)

        try:
            # Grab url & add torrent with label
            request = urllib.request.Request(update.message.text.strip(), headers=HEADERS)
            status_code = urllib.request.urlopen(request).getcode()
            if status_code == 200:
                file_contents = urllib.request.urlopen(request).read()
                tid = self.core.add_torrent_file(None, b64encode(file_contents), {})
                self.apply_label(tid, context)
                self.add_torrent_for_chat(chat_id=update.effective_chat.id, torrent_id=str(tid))
                return ConversationHandler.END

            else:
                update.message.reply_text(
                    text="Failed to download torrent file",
                    reply_markup=ReplyKeyboardRemove()
                )
        except Exception as e:
            update.message.reply_text(
                text="Failed to download torrent file",
                reply_markup=ReplyKeyboardRemove()
            )
            log.error(str(e) + '\n' + traceback.format_exc())

        return ConversationHandler.END

    def invalid_input_handler(self, update: Update, context: CallbackContext):
        update.message.reply_text(
            text="Invalid input. Terminating operation",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    def tg_middleware(self, update: Update, context: CallbackContext):
        if not self.chat_is_permitted(update.effective_chat.id):
            if update.message and update.message.text and update.message.text == '/start':
                update.message.reply_text(text="Unauthorized\nChat ID: %s" % update.effective_chat.id)

            raise DispatcherHandlerStop()

    #########
    #  Section: Helpers
    #########

    def load_available_labels(self):
        self.available_labels = []
        try:
            self.label_plugin = component.get('CorePlugin.Label')

            if self.label_plugin:
                for g in self.label_plugin.get_labels():
                    self.available_labels.append(g)

            # available_labels.append("No Label")

        except Exception as e:
            log.error("Label plugin not found. Delugram will continue without labels")

        return self.available_labels

    def apply_label(self, tid, context: CallbackContext):
        try:
            self.load_available_labels()
            label = context.chat_data.pop('label', None)

            if label is not None and label != "No Label" and self.label_plugin and label in self.available_labels:
                self.label_plugin.set_torrent(tid, label.lower())
                return True
            return False
        except Exception as e:
            log.error(str(e) + '\n' + traceback.format_exc())
            return False

    def add_torrent_for_chat(self, chat_id, torrent_id):
        chat_id = str(chat_id)
        torrent_id = str(torrent_id)

        if chat_id not in self.config['chat_torrents']:
            self.config['chat_torrents'][chat_id] = []

        if torrent_id not in self.config['chat_torrents'][chat_id]:
            self.config['chat_torrents'][chat_id].append(torrent_id)
            self.config.save()

    def remove_torrent_for_chat(self, torrent_id):
        torrent_id = str(torrent_id)

        for chat_id, torrents in self.config['chat_torrents'].items():
            if torrent_id in torrents:
                torrents.remove(torrent_id)
                if not torrents:  # Clean up empty lists
                    del self.config['chat_torrents'][chat_id]
                self.config.save()
                break

    def cleanup_chat_torrents(self):
        """
        Removes torrent IDs from chat_torrents mapping if they no longer exist in Deluge.
        """
        # Get active torrents from Deluge
        torrents = list(str(t) for t in self.torrent_manager.torrents.keys())

        if isinstance(torrents, list):
            # Iterate over chat_ids and remove any matching torrent_id from the list
            for chat_id in list(self.config['chat_torrents'].keys()):
                # Remove all non-matching torrent IDs
                for torrent_id in self.config['chat_torrents'][chat_id]:
                    if not torrent_id in torrents:
                        # self.config['chat_torrents'][chat_id].remove(torrent_id)
                        pass

            # self.config.save()

    def get_torrent_chat(self, torrent_id):
        for chat_id in self.config['chat_torrents']:
            if str(torrent_id) in self.config['chat_torrents'][chat_id]:
                return chat_id
        return None

    def list_torrents(self, filter_func):
        selected_torrents = []
        torrents = list(self.torrent_manager.torrents.values())
        for t in torrents:
            if filter_func(t):
                selected_torrents.append(self.format_torrent_info(t))
        if len(selected_torrents) == 0:
            return "No active torrents found"
        return "\n\n".join(selected_torrents)

    def format_torrent_info(self, torrent):
        try:
            status = torrent.get_status(INFOS)
            status_string = ''.join([f(status[i], status) for i, f in INFO_DICT if f is not None])
        except Exception as e:
            status_string = ''
        return status_string

    def chat_is_permitted(self, chat_id):
        return str(chat_id) in [item["chat_id"] for item in self.config['chats']]
