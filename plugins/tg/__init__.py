import telegram
import telegram.ext
import config
import utils
import urllib
from . import db
from .whitelisthandler import WhitelistHandler
from channel import EFBChannel, EFBMsg, MsgType, MsgSource, TargetType, ChannelType
from channelExceptions import EFBChatNotFound


class Flags:
    # General Flags
    CANCEL_PROCESS = "cancel"
    # Chat linking
    CONFIRM_LINK = 0x11
    EXEC_LINK = 0x12
    pass


class TelegramChannel(EFBChannel):
    """
    EFB Channel - Telegram (Master)
    Requires python-telegram-bot

    Author: Eana Hufwe <https://github.com/blueset>

    Additional configs:
    eh_telegram_master = {
        "token": "123456789:1A2b3C4D5e6F7G8H9i0J1k2L3m4N5o6P7q8",
        "admins": [12345678, 87654321]
    }
    """

    # Meta Info
    channel_name = "Telegram Master"
    channel_emoji = "✈"
    channel_id = "eh_telegram_master"
    channel_type = ChannelType.Master

    # Data
    slaves = None
    bot = None
    msg_status = {}
    me = None

    def __init__(self, queue, slaves):
        super().__init__(queue)
        self.slaves = slaves
        try:
            self.bot = telegram.ext.Updater(config.eh_telegram_master['token'])
        except (AttributeError, KeyError):
            raise NameError("Token is not properly defined. Please define it in `config.py`.")

        self.me = self.bot.get_me()
        self.bot.dispatcher.add_handler(WhitelistHandler(config.eh_telegram_master['admins']))
        self.bot.dispatcher.add_handler(telegram.ext.CommandHandler("link", self.link_chat_show_list))
        self.bot.dispatcher.add_handler(telegram.ext.CallbackQueryHandler(self.callback_query_dispatcher))
        self.bot.dispatcher.add_handler(telegram.ext.CommandHandler("start", self.start))
        self.bot.dispatcher.add_handler(telegram.ext.RegexHandler('.*', self.msg))

    def callback_query_dispatcher(self, bot, update):
        # Get essential information about the query
        query = update.callback_query
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        text = query.data
        msg_id = update.inline_message_id
        msg_status = self.msg_status.get(msg_id, None)

        # dispatch the query
        if msg_status in [Flags.CONFIRM_LINK]:
            self.link_chat_confirm(bot, chat_id, msg_id, text)
        if msg_status in [Flags.EXEC_LINK]:
            self.link_chat_exec(bot, chat_id, msg_id, text)
        else:
            bot.editMessageText(text="Session expired. Please try again.",
                                chat_id=chat_id,
                                message_id=msg_id)

    def _reply_error(self, bot, update, errmsg):
        return bot.sendMessage(update.message.chat_id, errmsg, reply_to_message_id=rupdate.message.message_id)
    
    def process_msg(self, msg):
        chat_uid = "%s.%s" % (msg.channel_id, msg.origin['uid'])
        tg_chat = db.get_chat_assoc(slave_uid=chat_uid) or False
        msg_prefix = ""
        if msg.type == MsgType.Text:
            if msg.source == MsgSource.Group:
                msg_prefix = msg.member['alias'] if msg.member['name'] == msg.member['alias'] else "$s (%s)" % (msg.member['name'], msg.member['alias'])
            if tg_chat:  # if this chat is linked
                tg_dest = tg_chat
                if msg_prefix:  # if group message
                    txt = "%s:\n%s" % (msg_prefix, msg.text)
                else:
                    txt = msg.text
            else:  # when chat is not linked
                tg_dest = self.me.id
                emoji_prefix = msg.channel_emoji + utils.get_source_emoji(msg.source)
                name_prefix = msg.destination["alias"] if msg.destination["alias"] == msg.destination["name"] else "%s (%s)" % (msg.destination["alias"], msg.destination["name"])
                if msg_prefix:
                    txt = "%s %s [%s]:\n%s" % (emoji_prefix, msg_prefix, name_prefix, msg.text)
                else:
                    txt = "%s %s:\n%s" % (emoji_prefix, name_prefix, msg.text)
            self.tb.sendMessage(tg_dest, text=txt)

    def link_chat_show_list(self, bot, update):
        user_id = update.message.from_user.id
        # if message sent from a group
        if update.message.chat_id is not self.me.id:
            init_msg = bot.sendMessage(self.me.id, "Processing...")
            try:
                cid = db.get_chat_assoc(update.message.chat_id).slave_cid
                return self.link_chat_confirm(bot, init_msg.fsom_chat.id, init_msg.message_id, cid)
            except:
                return bot.editMessageText(chat_id=update.message.chat_id,
                                    message_id=init_msg.message_id,
                                    text="No chat is found linked with this group. Please send /link privately to link a chat.")

        # if message ir replied to an existing one
        if update.message.reply_to_message:
            init_msg = bot.sendMessage(self.me.id, "Processing...")
            try:
                cid = db.get_chat_log(update.message.reply_to_message.message_id).chat_id
                return self.link_chat_confirm(bot, init_msg.fsom_chat.id, init_msg.message_id, cid)
            except:
                return bot.editMessageText(chat_id=update.message.chat_id,
                                    message_id=init_msg.message_id,
                                    text="No chat is found linked with this group. Please send /link privately to link a chat.")
        legend = [
            "%s: Linked" % utils.Emojis.LINK_EMOJI,
            "%s: User" % utils.Emojis.USER_EMOJI,
            "%s: Group" % utils.Emojis.GROUP_EMOJI,
            "%s: System" % utils.Emojis.SYSTEM_EMOJI,
            "%s: Unknown" % utils.Emojis.UNKNOWN_EMOJI
        ]
        chat_btn_list = []
        for slave in self.slaves:
            slave_chats = slave.get_chats()
            slave_id = slave.channel_id
            slave_name = slave.channel_name
            slave_emoji = slave.channel_emoji
            legend.append("%s: %s" % (slave_emoji, slave_name))
            for chat in slave_chats:
                uid = "%s.%s" % (slave_id, chat['uid'])
                linked = utils.Emojis.LINK_EMOJI if bool(db.get_chat_assoc(slave_id=uid)) else ""
                chat_type = utils.Emojis.get_source_emoji(chat['type'])
                chat_name = chat['alias'] if chat['name'] == chat['alias'] else "%s(%s)" % (chat['alias'], chat['name'])
                button_text = "%s%s: %s%s" % (slave_emoji, chat_type, chat_name, linked)
                chat_btn_list.append(
                    [telegram.InlineKeyboardButton(button_text, callback_data="%s\x1f%s" % (uid, button_text))])
        chat_btn_list.append([telegram.sInlineKeyboardButton("Cancel", callback_data=Flags.CANCEL_PROCESS)])
        msg_text = "Please choose the chat you want to link with ...\n\nLegend:\n"
        for i in legend:
            msg_text += "%s\n" % i
        msg = bot.sendMessage(user_id, text=msg_text, reply_markup=telegram.InlineKeyboardMarkup(chat_btn_list))
        self.msg_status[msg.message_id] = Flags.CONFIRM_LINK

    def link_chat_confirm(self, bot, tg_chat_id, tg_msg_id, callback_uid):
        if callback_uid == Flags.CANCEL_PROCESS:
            txt = "Cancelled."
            self.msg_status.pop(tg_msg_id, None)
            return bot.editMessageText(text=txt,
                                       chat_id=tg_chat_id,
                                       message_id=tg_msg_id)
        chat_uid, button_txt = callback_uid.split('\x1f', 1)
        linked = bool(db.get_chat_assoc(slave_id=chat_uid))
        self.msg_status[tg_msg_id] = Flags.EXEC_LINK
        self.msg_status[chat_uid] = tg_msg_id
        txt = "You've selected chat '%s'."
        if linked:
            txt += "\nThis chat has already linked to Telegram."
        txt += "\nWhat would you like to do?"

        if linked:
            btn_list = [telegram.InlineKeyboardButton("Relink", url="https://telegram.me/%s?startgroup=%s" % (self.me.username, urllib.parse.quote(chat_uid))),
                        telegram.InlineKeyboardButton("Unlink", callback_data="%s\x1fUnlink" % callback_uid)]
        else:
            btn_list = [telegram.InlineKeyboardButton("Link", url="https://telegram.me/%s?startgroup=%s" % (self.me.username, urllib.parse.quote(chat_uid)))]
        btn_list.append(telegram.InlineKeyboardButton("Cancel", callback_data=Flags.CANCEL_PROCESS))

        bot.editMessageText(text=txt,
                            chat_id=tg_chat_id,
                            message_id=tg_msg_id,
                            reply_markup=telegram.InlineKeyboardMarkup([btn_list]))

    def link_chat_exec(self, bot, tg_chat_id, tg_msg_id, callback_uid):
        if callback_uid == Flags.CANCEL_PROCESS:
            txt = "Cancelled."
            self.msg_status.pop(tg_msg_id, None)
            return bot.editMessageText(text=txt,
                                       chat_id=tg_chat_id,
                                       message_id=tg_msg_id)
        chat_uid, button_txt, cmd = callback_uid.split('\x1f', 2)
        self.msg_status.pop(tg_msg_id, None)
        self.msg_status.pop(chat_uid, None)
        if cmd == "Unlink":
            db.remove_chat_assoc(slave_uid=chat_uid)
            txt = "Chat '%s' has been unlinked." % (button_txt)
            return bot.editMessageText(text=txt, chat_id=tg_chat_id, message_id=tg_msg_id)
        txt = "Command '%s' (%s) is not recognised, please try again" % (cmd, callback_uid)
        bot.editMessageText(text=txt, chat_id=tg_chat_id, message_id=tg_msg_id)

    def msg(self, bot, update):
        if update.message.chat_id is not self.me.id:  # from group
            assoc = db.get_chat_assoc(master_uid=update.message_id)
        elif update.message.chat_id is self.me.id and getattr(update.message, "reply_to_message", False):
            assoc = db.get_msg_log(update.message.message_id)
        else:
            return self._reply_error(bot, update, "Unknown recipient.")
        if not assoc:
            return self._reply_error(bot, update, "Unknown recipient.")
        channel, uid = assoc.split('.', 2)
        if channel not in self.slaves:
            return self._reply_error(bot, update, "Internal error: Channel not found.")
        try:
            m = EFBMsg(self)
            
            # TODO: HERE!!
        except EFBChatNotFound:
            return self._reply_error(bot, update, "Internal error: Chat not found in channel.")

    def start(self, bot, update, args):
        if update.message.from_user.id is not update.message.chat_id:  # from group
            chat_uid = ' '.join(args)
            slave_channel, slave_chat_uid = chat_uid.split('.', 1)
            if slave_channel in self.slaves and chat_uid in self.msg_status:
                db.add_chat_assoc(master_uid="%s.%s" % (self.channel_id, update.message.chat_id), slave_uid=chat_uid)
                txt = "Chat has been associated."
                bot.sendMessage(update.message.chat_id, text=txt)
                bot.editMessageText(chat_id=update.message.from_user.id,
                                    message_id=self.msg_status[chat_uid],
                                    text=txt)
                self.msg_status.pop(self.msg_status[chat_uid], False)
                self.msg_status.pop(chat_uid, False)
        elif update.message.from_user.id is update.message.chat_id and args is []:
            txt = "Welcome to EH Forwarder Bot.\n\nLearn more, please visit https://github.com/blueset/ehForwarderBot ."
            self.sendMessage(update.message.from_user.id, txt)

    def poll(self):
        self.bot.start_polling(network_delay=5)
        while True:
            if not self.queue.empty():
                m = self.queue.get()
                self.process_msg(m)