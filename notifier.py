"""
Telegram 通知模块
  - 平仓成功后向指定 Telegram 频道/用户发送消息
  - 通过 .env 中的 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 配置
  - 若未配置则静默跳过（不影响主流程）
"""

import logging
import aiohttp

logger = logging.getLogger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """
    封装 Telegram Bot API 的异步通知器。
    若 bot_token 或 chat_id 为空，所有发送操作均为空操作。
    """

    def __init__(self, bot_token: str, chat_id: str):
        self._enabled = bool(bot_token and chat_id)
        self._url = _API_URL.format(token=bot_token)
        self._chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def send(self, text: str) -> bool:
        """
        发送消息到 Telegram。
        返回是否发送成功（未配置时直接返回 True）。
        """
        if not self._enabled:
            return True

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url,
                    json={
                        "chat_id": self._chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()

            if not data.get("ok"):
                logger.warning("Telegram 通知发送失败: %s", data)
                return False
            return True

        except Exception as e:
            logger.warning("Telegram 通知异常（不影响主流程）: %s", e)
            return False
