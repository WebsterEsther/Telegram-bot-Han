import os
import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse
import nest_asyncio

from config import config
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
    KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters
)

# 配置日志
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 应用事件循环补丁
nest_asyncio.apply()

@dataclass
class UserData:
    user_id: int
    username: str
    link: Optional[str] = None
    price_cny: Optional[float] = None
    shipping_method: Optional[str] = None
    contact: Optional[str] = None

    @property
    def price_rub(self) -> float:
        return self.price_cny * config.exchange_rate if self.price_cny else 0

class OrderService:
    @staticmethod
    async def send_notification(order_data: Dict, retries: int = 3) -> bool:
        email_settings = config.email_settings
        msg = EmailMessage()
        msg["Subject"] = f"Новый заказ от {order_data['username']}"
        msg["From"] = email_settings['address']
        msg["To"] = email_settings['address']
        msg.set_content(
            f"ID клиента: {order_data['user_id']}\n"
            f"Ссылка: {order_data['link']}\n"
            f"Цена: {order_data['price_cny']:.2f} CNY\n"
            f"Контакт: {order_data['contact']}"
        )

        for attempt in range(retries):
            try:
                with smtplib.SMTP_SSL(
                    email_settings['server'],
                    email_settings['port'],
                    timeout=email_settings['timeout']
                ) as server:
                    server.login(email_settings['address'], email_settings['password'])
                    server.send_message(msg)
                    return True
            except Exception as e:
                logging.error(f"邮件发送失败 (尝试 {attempt + 1}/{retries}): {str(e)}")
                await asyncio.sleep(2 ** attempt)
        return False

class States:
    LINK, PRICE, SHIPPING, CONTACT, CONFIRMATION = range(5)

class BotHandlers:
    def __init__(self, order_service: OrderService):
        self.order_service = order_service
        self.logger = logging.getLogger(__name__)

    # [保持所有处理方法不变，与之前相同]
    # ... (此处应包含所有原来的处理方法代码)

def setup_handlers(app):
    order_service = OrderService()
    handlers = BotHandlers(order_service)

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", handlers.start)],
        states={
            States.LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_link)],
            States.PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_price)],
            States.SHIPPING: [CallbackQueryHandler(handlers.handle_shipping, pattern="^ship_")],
            States.CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_contact)],
            States.CONFIRMATION: [MessageHandler(filters.Regex(r"^(да|нет)$"), handlers.handle_confirmation)]
        },
        fallbacks=[CommandHandler("cancel", handlers.cancel)]
    )
    
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("healthz", handlers.health_check))
    app.add_error_handler(handlers.handle_errors)

async def run_bot():
    app = ApplicationBuilder() \
        .token(config.token) \
        .concurrent_updates(True) \
        .http_version("1.1") \
        .build()

    setup_handlers(app)
    logger.info("Бот запущен и готов к работе")

    if os.getenv('RENDER'):
        await app.bot.set_webhook(
            url=f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{config.token}",
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True
        )
        await app.run_webhook(
            listen="0.0.0.0",
            port=int(os.getenv('PORT', 10000)),
            url_path=config.token,
            secret_token=os.getenv('WEBHOOK_SECRET', 'default_secret')
        )
    else:
        await app.run_polling()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except Exception as e:
        logger.critical(f"启动失败: {str(e)}", exc_info=True)
        raise
