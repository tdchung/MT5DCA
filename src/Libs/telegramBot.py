import time
import threading
from telegram import (
    ParseMode,
    Bot,
)

from Libs.log import log

##########################
MSG_HELP = """
<b>List commands:</b>
    - <code>/grid long 1</code>
    - <code>/grid short 1</code>
"""


class TelegramBot:
    def __init__(
            self,
            token : str,
            name=None,
            chat_ids=[],
        ):
        self.token = token
        self.name = name
        self.chat_ids = chat_ids

        self.bot = Bot(token=self.token)
        
    def send_message(self, msg, chat_id=None, symbol=None, reply_to_message_id=None):
        """
        Send a message in a separate thread. If reply_to_message_id is provided, send as a thread (reply).
        """
        def _send():
            keyboards = None
            if not chat_id:
                for _chat_id in self.chat_ids:
                    res = self.bot.send_message(
                        chat_id=_chat_id,
                        text=msg,
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboards,
                        reply_to_message_id=reply_to_message_id
                    )
                    log(res)
                return
            res = self.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboards,
                reply_to_message_id=reply_to_message_id
            )
            log(res)
        threading.Thread(target=_send, daemon=True).start()
            
    def send_photo(self, image_uri, msg, chat_id=None, symbol=None):
        def _send():
            keyboards = None
            if not chat_id:
                for _chat_id in self.chat_ids:
                    with open(image_uri, 'rb') as photo_file:
                        res = self.bot.send_photo(
                            chat_id=_chat_id,
                            photo=photo_file,
                            caption=msg,
                            reply_markup=keyboards,
                            parse_mode=ParseMode.HTML,
                        )
                        log(res)
                return
            with open(image_uri, 'rb') as photo_file:
                res = self.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=msg,
                    reply_markup=keyboards,
                    parse_mode=ParseMode.HTML,
                )
                log(res)
        threading.Thread(target=_send, daemon=True).start()