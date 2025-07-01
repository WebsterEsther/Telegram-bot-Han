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

    def _create_cancel_keyboard(self):
        return ReplyKeyboardMarkup([[KeyboardButton("/cancel")]], resize_keyboard=True)

    def _create_confirmation_keyboard(self):
        return ReplyKeyboardMarkup(
            [[KeyboardButton("Да"), KeyboardButton("Нет")]], 
            resize_keyboard=True
        )

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

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """取消当前对话"""
        await update.message.reply_text(
            "❌ Операция отменена",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.clear()
        return ConversationHandler.END

    async def _init_conversation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """初始化对话"""
        user = update.effective_user
        context.user_data.clear()
        context.user_data["user"] = UserData(
            user_id=user.id,
            username=user.username or user.full_name
        )

        await update.message.reply_text(
            f"👋 Здравствуйте, {user.first_name}!\n"
            "Отправьте ссылку на товар:",
            reply_markup=ReplyKeyboardRemove()
        )
        return States.LINK

    async def handle_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理商品链接"""
        text = update.message.text.strip()
        
        try:
            parsed = urlparse(text)
            if not all([parsed.scheme in ('http', 'https'), parsed.netloc]):
                raise ValueError("Invalid URL")
        except ValueError:
            await update.message.reply_text(
                "⚠️ Пожалуйста, отправьте корректную ссылку (начинающуюся с http:// или https://)",
                reply_markup=ReplyKeyboardRemove()
            )
            return States.LINK

        context.user_data["user"].link = text
        await update.message.reply_text(
            "🔗 Ссылка принята! Введите цену в CNY:",
            reply_markup=self._create_cancel_keyboard()
        )
        return States.PRICE

    async def handle_price(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理价格输入"""
        text = update.message.text.strip()
        
        try:
            price = float(text.replace(",", "."))
            if price <= 0:
                raise ValueError("Price must be positive")
            if price > 1000000:
                await update.message.reply_text(
                    "⚠️ Цена слишком высокая (максимум 1,000,000 CNY)",
                    reply_markup=ReplyKeyboardRemove()
                )
                return States.PRICE
                
            context.user_data["user"].price_cny = price
        except ValueError:
            await update.message.reply_text(
                "⚠️ Пожалуйста, введите корректную цену (например: 199.99)",
                reply_markup=ReplyKeyboardRemove()
            )
            return States.PRICE

        return await self._show_shipping_options(update, context)

    async def _show_shipping_options(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """显示运输选项"""
        shipping_options = config.shipping_options
        keyboard = [
            [InlineKeyboardButton(
                f"{opt['name']} - {opt['price_per_kg']}₽/кг ({opt['days']} дней)",
                callback_data=f"ship_{key}"
            )] for key, opt in shipping_options.items()
        ]
        
        await update.message.reply_text(
            f"💰 Итоговая цена: {context.user_data['user'].price_rub:.2f} RUB\n"
            "Выберите способ доставки:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return States.SHIPPING

    async def handle_shipping(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理运输方式选择"""
        query = update.callback_query
        await query.answer()
        
        method = query.data.split("_")[1]
        context.user_data["user"].shipping_method = method
        shipping_info = config.shipping_options[method]
        
        await query.edit_message_text(
            f"Выбрано: {shipping_info['name']}\n"
            f"Цена: {shipping_info['price_per_kg']}₽/кг\n"
            f"Срок: {shipping_info['days']} дней\n\n"
            "📧 Введите контактные данные (email/телефон):"
        )
        return States.CONTACT

    async def handle_contact(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理联系方式"""
        contact = update.message.text.strip()
        
        is_valid, message = self._validate_contact(contact)
        if not is_valid:
            await update.message.reply_text(
                f"⚠️ {message}",
                reply_markup=self._create_cancel_keyboard()
            )
            return States.CONTACT
        
        context.user_data["user"].contact = contact
        return await self._confirm_order(update, context)

    def _validate_contact(self, contact: str) -> Tuple[bool, str]:
        """验证联系方式格式"""
        contact = contact.strip()
        
        if "@" in contact:
            parts = contact.split("@")
            if len(parts) == 2 and "." in parts[1]:
                return True, ""
            return False, "Неверный формат email (пример: example@mail.com)"
        
        clean_phone = "".join(c for c in contact if c.isdigit())
        if len(clean_phone) >= 5:
            return True, ""
        
        return False, "Введите email или телефон (пример: +7 123 456 7890)"

    async def _confirm_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """订单确认"""
        user_data = context.user_data["user"]
        shipping = config.shipping_options[user_data.shipping_method]

        await update.message.reply_text(
            f"✅ Подтвердите заказ:\n\n"
            f"• Товар: {user_data.link}\n"
            f"• Цена: {user_data.price_cny:.2f} CNY ({user_data.price_rub:.2f} RUB)\n"
            f"• Доставка: {shipping['name']} ({shipping['price_per_kg']}₽/кг, {shipping['days']} дней)\n"
            f"• Контакт: {user_data.contact}\n\n"
            "Отправьте 'да' для подтверждения или 'нет' для отмены:",
            reply_markup=self._create_confirmation_keyboard()
        )
        return States.CONFIRMATION

    async def handle_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理最终确认"""
        text = update.message.text.lower()
        
        if text == "да":
            try:
                success = await self.order_service.send_notification(vars(context.user_data["user"]))
                if success:
                    await update.message.reply_text(
                        "🎉 Заказ оформлен! Спасибо!\n\n"
                        "Отправьте /start для нового расчета",
                        reply_markup=ReplyKeyboardRemove()
                    )
            except smtplib.SMTPAuthenticationError:
                await update.message.reply_text(
                    "⚠️ Ошибка сервера: проблема с аутентификацией почты",
                    reply_markup=ReplyKeyboardRemove()
                )
            except smtplib.SMTPConnectError:
                await update.message.reply_text(
                    "⚠️ Ошибка соединения с почтовым сервером",
                    reply_markup=ReplyKeyboardRemove()
                )
            except Exception as e:
                self.logger.exception("Неожиданная ошибка при подтверждении заказа")
                await update.message.reply_text(
                    "⚠️ Внутренняя ошибка системы. Пожалуйста, попробуйте позже",
                    reply_markup=ReplyKeyboardRemove()
                )
        else:
            await update.message.reply_text(
                "Заказ отменен. Отправьте /start для нового расчета",
                reply_markup=ReplyKeyboardRemove()
            )
        
        context.user_data.clear()
        return ConversationHandler.END

    async def handle_errors(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """全局错误处理"""
        self.logger.error(
            "Handler error: %s", 
            context.error,
            exc_info=not isinstance(context.error, (smtplib.SMTPException, ValueError)))
        
        if isinstance(update, Update) and update.message:
            await update.message.reply_text(
                "⚠️ Произошла ошибка. Используйте /start",
                reply_markup=ReplyKeyboardRemove()
            )

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
    app.add_handler(CommandHandler("healthz", handlers.health_check))
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
            await app.bot.set_webhook(
        url=f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{config.token}",
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True
    )
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
