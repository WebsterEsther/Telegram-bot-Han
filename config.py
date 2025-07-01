import os
from pathlib import Path
from dotenv import load_dotenv
import logging

class Config:
    """安全配置管理器"""
    
    def __init__(self):
        # 配置日志
        logging.basicConfig(
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            level=logging.INFO
        )
        self.logger = logging.getLogger(__name__)
        
        # 自动识别环境
        self.is_production = os.getenv('RENDER', 'false').lower() == 'true' or os.getenv('PRODUCTION', 'false').lower() in ('1', 'true')
        
        # 在Render上不使用.env文件，直接使用环境变量
        if not self.is_production:
            env_file = '.env.prod' if self.is_production else '.env'
            self.env_path = Path(__file__).resolve().parent / env_file
            
            if self.env_path.exists():
                load_dotenv(self.env_path)
                self.logger.info(f"Loaded environment from: {self.env_path}")
        
        self._validate()
        self.logger.info("所有配置验证通过")

    def _validate(self):
        """验证关键配置是否存在"""
        required = {
            'TELEGRAM_BOT_TOKEN': self.token,
            'ADMIN_EMAIL': self.email,
            'EMAIL_PASSWORD': self.email_password
        }
        
        missing = [name for name, value in required.items() if not value]
        if missing:
            error_msg = f"缺少必要配置: {', '.join(missing)}"
            self.logger.critical(error_msg)
            raise ValueError(error_msg)

    @property
    def token(self) -> str:
        """安全获取机器人Token"""
        return os.getenv('TELEGRAM_BOT_TOKEN', '').strip(' "\'')

    @property
    def email(self) -> str:
        """获取管理员邮箱"""
        return os.getenv('ADMIN_EMAIL', '').strip(' "\'')

    @property
    def email_password(self) -> str:
        """安全获取邮箱密码"""
        return os.getenv('EMAIL_PASSWORD', '').strip(' "\'')

    @property
    def email_settings(self) -> dict:
        """获取完整的邮箱设置"""
        return {
            'address': self.email,
            'password': self.email_password,
            'server': os.getenv('SMTP_SERVER', 'smtp.gmail.com'),
            'port': int(os.getenv('SMTP_PORT', 465)),
            'timeout': int(os.getenv('SMTP_TIMEOUT', 10))
        }

    @property
    def shipping_options(self) -> dict:
        """运输选项配置"""
        return {
            'truck': {
                'price_per_kg': 0,
                'days': '18-21',
                'name': '🚚 Грузовик (бесплатно)'
            },
            'air': {
                'price_per_kg': 1300,
                'days': '12-15',
                'name': '✈️ Авиа'
            },
            'express': {
                'price_per_kg': 2500,
                'days': '1-5',
                'name': '⚡️ Экспресс'
            }
        }

    @property
    def exchange_rate(self) -> float:
        """获取汇率"""
        return float(os.getenv('EXCHANGE_RATE', 13.0))

# 创建全局配置实例
config = Config()
