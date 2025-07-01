import os
import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

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
        """发送邮件通知"""
        email_settings = config.email_settings
        shipping_options = config.shipping_options
        
        msg = EmailMessage()
        msg["Subject"] = f"Новый заказ от {order_data['username']}"
        msg["From"] = email_settings['address']
        msg["To"] = email_settings['address']
        msg.set_content(
            f"Детали заказа:\n\n"
            f"ID клиента: {order_data['user_id']}\n"
            f"Ссылка: {order_data['link']}\n"
            f"Цена: {order_data['price_cny']:.2f} CNY → {order_data['price_rub']:.2f} RUB\n"
            f"Доставка: {shipping_options[order_data['shipping_method']]['name']}\n"
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
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
        return False

class States:
    LINK, PRICE, SHIPPING, CONTACT, CONFIRMATION = range(5)

class BotHandlers:
    def __init__(self, order_service: OrderService):
        self.order_service = order_service
        self.logger = logging.getLogger(__name__)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """开始对话"""
        return await self._init_conversation(update, context)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """显示帮助信息"""
        await update.message.reply_text(
            "📝 Доступные команды:\n"
            "/start - Начать новый расчет\n"
            "/cancel - Отменить текущий запрос\n"
            "/help - Показать это сообщение"
        )

    async def health_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """健康检查端点"""
        await update.message.reply_text("🟢 Bot is healthy")
        return ConversationHandler.END

    # ... (保持原有其他方法不变，完整代码见上文) ...

def setup_handlers(app):
    order_service = OrderService()
    handlers = BotHandlers(order_service)

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", handlers.start),
            CommandHandler("help", handlers.help_command)
        ],
        states={
            States.LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_link),
                CommandHandler("cancel", handlers.cancel)
            ],
            States.PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_price),
                CommandHandler("cancel", handlers.cancel)
            ],
            States.SHIPPING: [
                CallbackQueryHandler(handlers.handle_shipping, pattern="^ship_"),
                CommandHandler("cancel", handlers.cancel)
            ],
            States.CONTACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_contact),
                CommandHandler("cancel", handlers.cancel)
            ],
            States.CONFIRMATION: [
                MessageHandler(filters.Regex(r"^(да|нет)$"), handlers.handle_confirmation),
                CommandHandler("cancel", handlers.cancel)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", handlers.cancel),
            CommandHandler("help", handlers.help_command)
        ],
        allow_reentry=True
    )
    
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("healthz", handlers.health_check))  # 添加健康检查端点
    app.add_error_handler(handlers.handle_errors)

def main():
    """启动入口"""
    try:
        # 创建应用
        app = ApplicationBuilder() \
            .token(config.token) \
            .concurrent_updates(True) \
            .http_version("1.1") \
            .get_updates_http_version("1.1") \
            .build()

        # 设置处理器
        setup_handlers(app)

        # 启动机器人
        logger.info("Бот запущен и готов к работе")
        
        # 在Render上使用webhook
        if os.getenv('RENDER'):
            port = int(os.getenv('PORT', 10000))
            app.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=config.token,
                webhook_url=f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}",
                secret_token=os.getenv('WEBHOOK_SECRET', 'default_secret')
            )
        else:
            app.run_polling()
            
    except Exception as e:
        logger.critical(f"启动失败: {str(e)}", exc_info=True)
        raise

if __name__ == "__main__":
    main()
