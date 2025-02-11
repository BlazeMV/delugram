from __future__ import unicode_literals

import html
import json
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
from telegram.ext import (Updater, CommandHandler, CallbackContext, ConversationHandler, MessageHandler, Filters)
from telegram.utils.request import Request


DEFAULT_PREFS = {
    "telegram_token": "Contact @BotFather, create a new bot and get a bot token",
    "admin_chat_id": "Telegram chat id of the administrator. Use @userinfobot to get the chat id",
    "users": [],
    "active_torrents": {},
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
             ('name', lambda i, s: ' %s *%s* ' %
              (s['state'] if s['state'].lower() not in EMOJI
               else EMOJI[s['state'].lower()],
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

    def enable(self):
        self.config = deluge.configmanager.ConfigManager(
            'delugram.conf', DEFAULT_PREFS)

        # hydrate
        self.telegram_token = self.config['telegram_token']
        self.admin_chat_id = self.config['admin_chat_id']
        self.users = self.config['users']
        self.active_torrents = self.config['active_torrents']
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
            }
        ]

        self.torrent_manager = component.get("TorrentManager")
        self.event_manager = component.get("EventManager")
        self.label_plugin = None
        self.available_labels = self.load_available_labels()

        # check if the telegram token is set, if not, no need to go any further
        if self.telegram_token == DEFAULT_PREFS['telegram_token']:
            return

        # initialize telegram bot
        self.bot = Bot(self.telegram_token, request=Request(con_pool_size=8))
        self.updater = Updater(bot=self.bot, use_context=True)
        self.dispatcher = self.updater.dispatcher

        # register command handlers to telegram
        for cmd in self.commands:
            self.dispatcher.add_handler(cmd['handler'])
        # self.dp.add_handler(CommandHandler('help', self.tg_cmd_help))

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

        log.info("Starting to poll")

        # start polling
        self.updater.start_polling(poll_interval=1,allowed_updates=[Update.MESSAGE])

        log.info("Polling started")

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

    #########
    #  Section: Event Handlers
    #########

    def _on_torrent_added(self, torrent_id):
        """
        This is called when a torrent is added.
        """

    def _on_torrent_removed(self, torrent_id):
        """
        This is called when a torrent is removed.
        """
        self.deregister_torrent(torrent_id)

    def _on_torrent_finished(self, torrent_id):
        """
        This is called when a torrent is finished.
        """

    def tg_on_error(self, update: object, context: CallbackContext) -> None:
        """Log the error and send a telegram message to notify the developer."""
        # Log the error before we do anything else, so we can see it even if something breaks.
        log.error("Exception while handling an update:", exc_info=context.error)

        if not self.admin_chat_id:
            return

        # traceback.format_exception returns the usual python message about an exception, but as a
        # list of strings rather than a single string, so we have to join them together.
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)

        # Build the message with some markup and additional information about what happened.
        update_str = update.to_dict() if isinstance(update, Update) else str(update)
        message = (
            "An exception was raised while handling an update\n"
            f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}"
            "</pre>\n\n"
            f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
            f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
            f"<pre>{html.escape(tb_string)}</pre>"
        )

        #deal with the message length > 4000 (4090 is the limit)
        if len(message) > 4000:
            message = message[:4000] + '...'

        # Finally, send the message
        context.bot.send_message(
            chat_id=self.admin_chat_id, text=message, parse_mode=ParseMode.HTML
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
        update.message.reply_text(
            text= 'No active torrents found',
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

    def add_command_handler(self, update: Update, context: CallbackContext):
        # refresh available labels list
        self.load_available_labels()

        if len(self.available_labels):
            return self.advance_to_set_label_state(update=update, context=context)

        # if no labels are found, skip the label selection step
        else:
            return self.advance_to_torrent_type_state(update=update, context=context)

    def advance_to_set_label_state(self, update: Update, context: CallbackContext):
        session_msg = context.user_data.pop('message', '')
        keyboard_options = [[g] for g in self.available_labels]

        update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Select a label",
            reply_markup=ReplyKeyboardMarkup(keyboard_options, one_time_keyboard=True)
            # reply_to_message_id=update.message.message_id
        )
        return SET_LABEL_STATE

    def set_label_state_handler(self, update: Update, context: CallbackContext):
        if update.message.text in self.available_labels:
            context.user_data['label'] = update.message.text

            return self.advance_to_torrent_type_state(update=update, context=context)
        else:
            context.user_data['message'] = "Invalid label. Try again"
            return self.advance_to_set_label_state(update=update, context=context)

    def advance_to_torrent_type_state(self, update: Update, context: CallbackContext):
        session_msg = context.user_data.pop('message', '')
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
        session_msg = context.user_data.pop('message', '')
        update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Send the magnet link",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADD_MAGNET_STATE

    def torrent_type_state_torrent_handler(self, update: Update, context: CallbackContext):
        return self.advance_to_add_torrent_state(update=update, context=context)

    def advance_to_add_torrent_state(self, update: Update, context: CallbackContext):
        session_msg = context.user_data.pop('message', '')
        update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Send the torrent file",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADD_TORRENT_STATE

    def torrent_type_state_url_handler(self, update: Update, context: CallbackContext):
        return self.advance_to_add_url_state(update=update, context=context)

    def advance_to_add_url_state(self, update: Update, context: CallbackContext):
        session_msg = context.user_data.pop('message', '')
        update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Send the torrent url",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADD_URL_STATE

    def torrent_type_state_unknown_handler(self, update: Update, context: CallbackContext):
        context.user_data['message'] = "Invalid option. Try again"
        return self.advance_to_torrent_type_state(update=update, context=context)

    def add_magnet_state_handler(self, update: Update, context: CallbackContext):
        if not is_magnet(update.message.text):
            context.user_data['message'] = "Invalid magnet link. Try again"
            return self.advance_to_add_magnet_state(update=update, context=context)

        tid = component.get('Core').add_torrent_magnet(update.message.text, {})
        self.apply_label(tid=tid, context=context)
        self.register_torrent_owner(tid, update.effective_chat.id)

        return ConversationHandler.END

    def add_torrent_state_handler(self, update: Update, context: CallbackContext):
        if update.message.document.mime_type != 'application/x-bittorrent':
            context.user_data['message'] = "Invalid torrent file. Try again"
            return self.advance_to_add_torrent_state(update=update, context=context)

        try:
            # Grab file & add torrent with label
            file_info = self.bot.getFile(update.message.document.file_id)
            request = urllib.request.Request(file_info.file_path, headers=HEADERS)
            status_code = urllib.request.urlopen(request).getcode()
            if status_code == 200:
                file_contents = urllib.request.urlopen(request).read()
                tid = component.get('Core').add_torrent_file(None, b64encode(file_contents), {})
                self.apply_label(tid, context)
                self.register_torrent_owner(tid, update.effective_chat.id)
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
            context.user_data['message'] = "Invalid URL. Try again"
            return self.advance_to_add_url_state(update=update, context=context)

        try:
            # Grab url & add torrent with label
            request = urllib.request.Request(update.message.text.strip(), headers=HEADERS)
            status_code = urllib.request.urlopen(request).getcode()
            if status_code == 200:
                file_contents = urllib.request.urlopen(request).read()
                tid = component.get('Core').add_torrent_file(None, b64encode(file_contents), {})
                self.apply_label(tid, context)
                self.register_torrent_owner(tid, update.effective_chat.id)
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
            label = context.user_data.pop('label', None)

            if label is not None and label != "No Label" and self.label_plugin and label in self.available_labels:
                self.label_plugin.set_torrent(tid, label.lower())
                return True
            return False
        except Exception as e:
            log.error(str(e) + '\n' + traceback.format_exc())
            return False

    def register_torrent_owner(self, tid, chat_id):
        if chat_id not in self.active_torrents:
            self.active_torrents[chat_id] = []

        if chat_id not in self.active_torrents[tid]:
            self.active_torrents[chat_id].append(tid)

    def deregister_torrent(self, tid):
        for chat_id in self.active_torrents:
            if tid in self.active_torrents[chat_id]:
                self.active_torrents[chat_id].remove(tid)

