from __future__ import unicode_literals

import html
import json
import traceback
from delugram.logger import log
import deluge.configmanager
from deluge import component
from deluge.common import fsize, ftime, fdate, fpeer, fpcnt, fspeed
from deluge.core.rpcserver import export
from deluge.plugins.pluginbase import CorePluginBase
from telegram import (Bot, Update, ParseMode, ReplyKeyboardRemove)
from telegram.ext import (Updater, CommandHandler, CallbackContext, ConversationHandler, MessageHandler, Filters)
from telegram.utils.request import Request

DEFAULT_PREFS = {
    "telegram_token": "Contact @BotFather, create a new bot and get a bot token",
    "admin_chat_id": "Telegram chat id of the administrator. Use @userinfobot to get the chat id",
    "users": [],
    "active_torrents": [],
}

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
                         '(KHTML, like Gecko) Chrome/74.0.3729.169 Safari/537.36'}

SET_LABEL, TORRENT_TYPE, ADD_MAGNET, ADD_TORRENT, ADD_URL = list(range(5))

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
                'handler': CommandHandler('start', self.tg_cmd_help),
                'list_in_help': False
            },
            {
                'name': 'add',
                'description': 'Add a new torrent',
                'handler': ConversationHandler(
                    entry_points=[CommandHandler('add', self.tg_cmd_help)],
                    states={
                        SET_LABEL: [MessageHandler(Filters.text, self.tg_cmd_help)],
                        TORRENT_TYPE: [MessageHandler(Filters.text, self.tg_cmd_help)],
                        ADD_MAGNET: [MessageHandler(Filters.text, self.tg_cmd_help)],
                        ADD_TORRENT: [MessageHandler(Filters.document, self.tg_cmd_help)],
                        ADD_URL: [MessageHandler(Filters.text, self.tg_cmd_help)]
                    },
                    fallbacks=[CommandHandler('cancel', self.tg_cmd_cancel)]
                ),
                'list_in_help': True
            },
            {
                'name': 'status',
                'description': 'Show status of active torrents',
                'handler': CommandHandler('status', self.tg_cmd_status),
                'list_in_help': True
            },
            {
                'name': 'cancel',
                'description': 'Cancels the current operation',
                'handler': CommandHandler('cancel', self.tg_cmd_cancel),
                'list_in_help': True
            },
            {
                'name': 'help',
                'description': 'List all available commands',
                'handler': CommandHandler('help', self.tg_cmd_help),
                'list_in_help': True
            }
        ]

        self.torrent_manager = component.get("TorrentManager")
        self.event_manager = component.get("EventManager")

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
            'TorrentFinishedEvent', self._on_torrent_finished
        )

        log.info("Starting to poll")

        # start polling
        self.updater.start_polling(poll_interval=1)

        log.info("Polling started")

        log.debug('Plugin enabled.')

    def disable(self):
        self.config.save()

        # unregister torrent download finished event listener
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

    def _on_torrent_finished(self, torrent_id):
        """
        This is called when a torrent finishes downloading.
        """
        tid = self.torrent_manager.torrents[torrent_id]
        tid_status = tid.get_status(['download_location', 'name'])

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

    def tg_cmd_help(self, update: Update, context: CallbackContext):
        help_msg = [
            f"/{cmd['name']} - {cmd['description']}" for cmd in self.commands if cmd['list_in_help']
        ]
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text='\n'.join(help_msg),
            parse_mode='Markdown',
            # reply_to_message_id=update.message.message_id
        )

    def tg_cmd_status(self, update: Update, context: CallbackContext):
        context.bot.send_message(
            text= 'No active torrents found',
            chat_id=update.effective_chat.id,
            parse_mode='Markdown'
            # reply_to_message_id=update.message.message_id
        )

    def tg_cmd_cancel(self, update: Update, context: CallbackContext):
        context.bot.send_message(
            text='Operation cancelled',
            chat_id=update.effective_chat.id,
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()
            # reply_to_message_id=update.message.message_id
        )
        return ConversationHandler.END

    #########
    #  Section: Helpers
    #########