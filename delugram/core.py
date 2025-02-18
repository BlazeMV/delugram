from __future__ import unicode_literals

import html
import json
import traceback
import urllib
from base64 import b64encode
from typing import Any, Dict, List, Optional

import asyncio
import threading

from telegram import Update, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, ContextTypes, filters, \
    Application, ApplicationBuilder

from delugram.logger import log

import deluge.configmanager
from deluge import component
from deluge.common import fsize, ftime, fdate, fpeer, fpcnt, fspeed, is_magnet, is_url
from deluge.core.rpcserver import export
from deluge.plugins.pluginbase import CorePluginBase



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

class InvalidTokenError(Exception):
    def __init__(self, message: str = "Invalid token provided or token not set"):
        super().__init__(message)


class Core(CorePluginBase):
    def __init__(self, plugin_name):
        super().__init__(plugin_name)

        self.core: Optional[Any] = None
        self.torrent_manager: Optional[Any] = None
        self.event_manager: Optional[Any] = None
        self.label_plugin: Optional[Any] = None
        self.available_labels: Optional[List[str]] = None
        self.config: Optional[Any] = None
        self.telegram: Optional[Application] = None
        self.commands: Optional[Dict[str, Any]] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None

    def enable(self, throw_polling_exceptions=False):
        # hydrate
        self.core = component.get('Core')
        self.config = deluge.configmanager.ConfigManager('delugram.conf', DEFAULT_PREFS)
        self.torrent_manager = component.get("TorrentManager")
        self.event_manager = component.get("EventManager")
        self.label_plugin = None
        self.available_labels = self.load_available_labels()

        try:
            self.initialize_telegram_bot()
            self.start_telegram_polling()
        except InvalidTokenError:
            log.error(f"Invalid telegram bot api token provided or token not set. Telegram will not be initialized \
                        during Delugram enable. Please set a valid token in the plugin preferences and restart \
                        Delugram.")

        self.register_deluge_event_handlers()

        log.debug('Plugin enabled.')

    def disable(self):
        self.config.save()

        self.stop_telegram_polling()

        self.deregister_deluge_event_handlers()

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

    async def _on_torrent_added(self, torrent_id, from_state=False):
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
        await self.telegram.bot.send_message(chat_id=owner, text=message, parse_mode=ParseMode.HTML)

    def _on_torrent_removed(self, torrent_id):
        """
        This is called when a torrent is removed.
        """
        self.cleanup_chat_torrents()

    async def _on_torrent_finished(self, torrent_id):
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
        await self.bot.send_message(chat_id=owner, text=message, parse_mode=ParseMode.HTML)

    async def tg_on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        await context.bot.send_message(
            chat_id=self.config['admin_chat_id'], text=message, parse_mode=ParseMode.HTML
        )

        #notify original chat of the error
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="An error occurred. Administrator has been notified.",
            reply_markup=ReplyKeyboardRemove()
        )

    #########
    #  Section: Telegram Helpers
    #########

    def define_telegram_commands(self):
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
                            MessageHandler(filters.TEXT & ~filters.COMMAND, self.set_label_state_handler),
                            MessageHandler(filters.ALL & ~filters.COMMAND, self.invalid_input_handler),
                        ],
                        TORRENT_TYPE_STATE: [
                            MessageHandler(filters.Regex("^Magnet$"), self.torrent_type_state_magnet_handler),
                            MessageHandler(filters.Regex(r"^\.torrent$"), self.torrent_type_state_torrent_handler),
                            MessageHandler(filters.Regex("^URL$"), self.torrent_type_state_url_handler),
                            MessageHandler(filters.ALL & ~filters.COMMAND, self.torrent_type_state_unknown_handler),
                        ],
                        ADD_MAGNET_STATE: [
                            MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_magnet_state_handler),
                            MessageHandler(filters.ALL & ~filters.COMMAND, self.invalid_input_handler),
                        ],
                        ADD_TORRENT_STATE: [
                            MessageHandler(filters.Document.FileExtension('.torrent'), self.add_torrent_state_handler),
                            MessageHandler(filters.ALL & ~filters.COMMAND, self.invalid_input_handler),
                        ],
                        ADD_URL_STATE: [
                            MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_url_state_handler),
                            MessageHandler(filters.ALL & ~filters.COMMAND, self.invalid_input_handler),
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
        return self.commands

    def is_telegram_token_set(self):
        if self.config['telegram_token'] == DEFAULT_PREFS['telegram_token'] or \
                self.config['telegram_token'] == '':
            return False
        return True

    def initialize_telegram_bot(self):
        if self.telegram:
            raise RuntimeError("Telegram bot already initialized")

        if not self.is_telegram_token_set():
            raise InvalidTokenError()

        self.define_telegram_commands()

        self.telegram = ApplicationBuilder().token(self.config['telegram_token']).build()

        # register tg middleware
        self.telegram.add_handler(MessageHandler(filters.ALL, self.tg_middleware), group=0)

        # register command handlers to telegram
        for cmd in self.commands:
            self.telegram.add_handler(cmd['handler'], group=1)

        # register error handlers to telegram
        self.telegram.add_error_handler(self.tg_on_error)

    async def start_telegram_bot(self):
        await self.telegram.initialize()
        await self.telegram.start()
        await self.telegram.updater.start_polling(poll_interval=0.5)

        log.info("Telegram Bot started with polling in a separate thread using asyncio event loops")

    async def stop_telegram_bot(self):
        await self.telegram.stop()  # Stop PTB gracefully

        # Stop the event loop safely
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)  # Stop the loop from the main thread

    def start_telegram_polling(self):
        if not self.telegram:
            raise RuntimeError("Telegram bot not initialized. Please call initialize_telegram_bot() first")

        if self.telegram.updater.running:
            log.warning("Telegram bot already polling. continuing...")
            return

        # start polling
        log.debug("Starting to poll")

        # Start the thread with the new event loop
        self.thread = threading.Thread(target=self.run_asyncio_loop, daemon=True)
        self.thread.start()

        log.debug("Polling started")

    def stop_telegram_polling(self):
        if not self.telegram:
            log.warning("Telegram bot not initialized. As such, it is not polling already. continuing...")
            return

        if not self.telegram.updater.running:
            log.warning("Telegram bot not polling already. continuing...")
            return

        log.debug("Stopping Telegram bot polling...")

        if self.loop:
            asyncio.run_coroutine_threadsafe(self.stop_telegram_bot(), self.loop)  # Run stop_bot() safely in the loop

        if self.thread:
            self.thread.join(timeout=5)  # Wait up to 5 seconds for thread to stop

        log.debug("Telegram bot polling stopped.")

    def run_asyncio_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.loop.run_until_complete(self.start_telegram_bot())  # Run the bot inside this loop
        self.loop.run_forever()  # Keep the loop running

    #########
    #  Section: Telegram Commands
    #########

    async def help_command_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_msg = [
            f"/{cmd['name']} - {cmd['description']}" for cmd in self.commands if cmd['list_in_help']
        ]
        await update.message.reply_text(
            text='\n'.join(help_msg),
            parse_mode='Markdown',
            # reply_to_message_id=update.message.message_id
        )

    async def status_command_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_torrents = self.config['chat_torrents'].get(str(update.effective_chat.id), [])
        message = self.list_torrents(lambda t:
                               t.get_status(('state',))['state'] in
                               ('Active', 'Downloading', 'Seeding',
                                'Paused', 'Checking', 'Error', 'Queued'
                                ) and str(t.torrent_id) in chat_torrents)

        await update.message.reply_text(
            text=message,
            parse_mode='Markdown'
            # reply_to_message_id=update.message.message_id
        )

    async def cancel_command_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            text='Operation cancelled',
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()
            # reply_to_message_id=update.message.message_id
        )
        return ConversationHandler.END

    async def register_command_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_chat.id) != self.config['admin_chat_id']:
            return

        args = update.message.text.split(sep=' ', maxsplit=3)

        if len(args) != 4:
            await update.message.reply_text(
                text="Invalid arguments. Usage: /register <bot_token> <chat_id> <chat_name>"
            )
            return

        if args[1] != self.config['telegram_token']:
            await update.message.reply_text(
                text="Invalid bot token. Usage: /register <bot_token> <chat_id> <chat_name>"
            )
            return

        if self.add_chat(chat_id=args[2], name=args[3]):
            await update.message.reply_text(
                text="Chat registered successfully\nChat ID: %s\nChat Name: %s" % (args[2], args[3])
            )
        else:
            await update.message.reply_text(
                text="Chat ID already registered"
            )

    async def deregister_command_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_chat.id) != self.config['admin_chat_id']:
            return

        args = update.message.text.split(sep=' ', maxsplit=2)

        if len(args) != 3:
            await update.message.reply_text(
                text="Invalid arguments. Usage: /deregister <bot_token> <chat_id>"
            )
            return

        if args[1] != self.config['telegram_token']:
            await update.message.reply_text(
                text="Invalid bot token. Usage: /deregister <bot_token> <chat_id>"
            )
            return

        if self.remove_chat(chat_id=args[2]):
            await update.message.reply_text(
                text="Chat deregistered successfully\nChat ID: %s" % (args[2])
            )
        else:
            await update.message.reply_text(
                text="Something went wrong"
            )

    async def add_command_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # refresh available labels list
        self.load_available_labels()

        if len(self.available_labels):
            return self.advance_to_set_label_state(update=update, context=context)

        # if no labels are found, skip the label selection step
        else:
            return self.advance_to_torrent_type_state(update=update, context=context)

    async def advance_to_set_label_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        session_msg = context.chat_data.pop('message', '')
        keyboard_options = [[g] for g in self.available_labels]

        await update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Select a label",
            reply_markup=ReplyKeyboardMarkup(keyboard_options, one_time_keyboard=True)
            # reply_to_message_id=update.message.message_id
        )
        return SET_LABEL_STATE

    async def set_label_state_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.text in self.available_labels:
            context.chat_data['label'] = update.message.text

            return self.advance_to_torrent_type_state(update=update, context=context)
        else:
            context.chat_data['message'] = "Invalid label. Try again"
            return self.advance_to_set_label_state(update=update, context=context)

    async def advance_to_torrent_type_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        session_msg = context.chat_data.pop('message', '')
        keyboard_options = [['Magnet'], ['.torrent'], ['URL']]

        await update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Select type of torrent source",
            reply_markup=ReplyKeyboardMarkup(keyboard_options, one_time_keyboard=True),
            # reply_to_message_id=update.message.message_id
        )
        return TORRENT_TYPE_STATE

    async def torrent_type_state_magnet_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return self.advance_to_add_magnet_state(update=update, context=context)

    async def advance_to_add_magnet_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        session_msg = context.chat_data.pop('message', '')
        await update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Send the magnet link",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADD_MAGNET_STATE

    async def torrent_type_state_torrent_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return self.advance_to_add_torrent_state(update=update, context=context)

    async def advance_to_add_torrent_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        session_msg = context.chat_data.pop('message', '')
        await update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Send the torrent file",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADD_TORRENT_STATE

    async def torrent_type_state_url_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return self.advance_to_add_url_state(update=update, context=context)

    async def advance_to_add_url_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        session_msg = context.chat_data.pop('message', '')
        await update.message.reply_text(
            text=(f"{session_msg}\n\n" if session_msg else "") + "Send the torrent url",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADD_URL_STATE

    async def torrent_type_state_unknown_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.chat_data['message'] = "Invalid option. Try again"
        return self.advance_to_torrent_type_state(update=update, context=context)

    async def add_magnet_state_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_magnet(update.message.text):
            context.chat_data['message'] = "Invalid magnet link. Try again"
            return self.advance_to_add_magnet_state(update=update, context=context)

        try:
            tid = self.core.add_torrent_magnet(update.message.text, {})
            self.apply_label(tid=tid, context=context)
            self.add_torrent_for_chat(chat_id=update.effective_chat.id, torrent_id=str(tid))
            return ConversationHandler.END

        except Exception as e:
            await update.message.reply_text(
                text="Failed to add magnet link",
                reply_markup=ReplyKeyboardRemove()
            )
            log.error(str(e) + '\n' + traceback.format_exc())

        return ConversationHandler.END

    async def add_torrent_state_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                await update.message.reply_text(
                    text="Failed to download torrent file. terminating operation",
                    reply_markup=ReplyKeyboardRemove()
                )
        except Exception as e:
            await update.message.reply_text(
                text="Failed to download torrent file. terminating operation",
                reply_markup=ReplyKeyboardRemove()
            )
            log.error(str(e) + '\n' + traceback.format_exc())

        return ConversationHandler.END

    async def add_url_state_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                await update.message.reply_text(
                    text="Failed to download torrent file",
                    reply_markup=ReplyKeyboardRemove()
                )
        except Exception as e:
            await update.message.reply_text(
                text="Failed to download torrent file",
                reply_markup=ReplyKeyboardRemove()
            )
            log.error(str(e) + '\n' + traceback.format_exc())

        return ConversationHandler.END

    async def invalid_input_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            text="Invalid input. Terminating operation",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    async def tg_middleware(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.chat_is_permitted(update.effective_chat.id):
            if update.message and update.message.text and update.message.text == '/start':
                await update.message.reply_text(text="Unauthorized\nChat ID: %s" % update.effective_chat.id)

            raise Exception("Unauthorized chat")

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

    def apply_label(self, tid, context: ContextTypes.DEFAULT_TYPE):
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

    def register_deluge_event_handlers(self):
        self.event_manager.register_event_handler(
            'TorrentAddedEvent', self._on_torrent_added
        )
        self.event_manager.register_event_handler(
            'TorrentRemovedEvent', self._on_torrent_removed
        )
        self.event_manager.register_event_handler(
            'TorrentFinishedEvent', self._on_torrent_finished
        )

    def deregister_deluge_event_handlers(self):
        self.event_manager.deregister_event_handler(
            'TorrentAddedEvent', self._on_torrent_added
        )
        self.event_manager.deregister_event_handler(
            'TorrentRemovedEvent', self._on_torrent_removed
        )
        self.event_manager.deregister_event_handler(
            'TorrentFinishedEvent', self._on_torrent_finished
        )