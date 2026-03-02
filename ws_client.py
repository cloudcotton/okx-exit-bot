"""
OKX WebSocket 客户端
  - 订阅多个合约的 K 线频道
  - 自动重连（断线后重新订阅，并通知调用方重新初始化缓冲区）
"""

import asyncio
import json
import logging
from typing import Callable, Awaitable, Dict, List, Optional

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

_WS_PUBLIC_URL = "wss://ws.okx.com:8443/ws/v5/business"

# 重连等待时间（秒），断线后逐步增加
_RECONNECT_DELAYS = [3, 5, 10, 30, 60]

# OKX 要求每隔 ≤30 秒发送一次心跳
_PING_INTERVAL = 20


class OKXWebSocketClient:
    """
    订阅 OKX 公开 WebSocket 的 K 线频道。

    on_candle 回调签名：
        async def handler(channel: str, inst_id: str, raw: list) -> None
    on_reconnect 回调签名（可选，用于重建缓冲区）：
        async def on_reconnect() -> None
    """

    def __init__(
        self,
        on_candle: Callable[[str, str, list], Awaitable[None]],
        on_reconnect: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self._on_candle = on_candle
        self._on_reconnect = on_reconnect
        self._subscriptions: List[Dict[str, str]] = []

    def add_subscription(self, channel: str, inst_id: str):
        """注册一个 K 线频道订阅。"""
        self._subscriptions.append({"channel": channel, "instId": inst_id})

    async def _subscribe(self, ws):
        msg = {"op": "subscribe", "args": self._subscriptions}
        await ws.send(json.dumps(msg))
        logger.info("已发送订阅请求，共 %d 个频道", len(self._subscriptions))

    async def _handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        # 事件消息（订阅确认、错误等）
        if "event" in msg:
            event = msg["event"]
            if event == "subscribe":
                logger.info("订阅成功: %s", msg.get("arg"))
            elif event == "error":
                logger.error("WebSocket 错误事件: %s", msg)
            return

        # 行情推送
        arg = msg.get("arg", {})
        channel = arg.get("channel", "")
        inst_id = arg.get("instId", "")
        data_list = msg.get("data", [])

        for item in data_list:
            await self._on_candle(channel, inst_id, item)

    async def run(self):
        """
        启动 WebSocket 连接，断线后自动重连。
        此协程永久运行，直到外部取消。
        """
        attempt = 0

        while True:
            delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
            is_reconnect = attempt > 0
            if is_reconnect:
                logger.info("等待 %d 秒后重连...", delay)
                await asyncio.sleep(delay)

            try:
                async with websockets.connect(
                    _WS_PUBLIC_URL,
                    ping_interval=_PING_INTERVAL,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    attempt = 0  # 连接成功，重置计数
                    logger.info("WebSocket 已连接: %s", _WS_PUBLIC_URL)
                    await self._subscribe(ws)

                    # 仅在真正重连时通知调用方重新加载历史K线（首次连接跳过）
                    if is_reconnect and self._on_reconnect is not None:
                        await self._on_reconnect()

                    async for message in ws:
                        await self._handle_message(message)

            except ConnectionClosed as e:
                logger.warning("WebSocket 连接关闭: %s", e)
            except (OSError, ConnectionError) as e:
                logger.warning("WebSocket 网络错误: %s", e)
            except asyncio.CancelledError:
                logger.info("WebSocket 任务已取消")
                raise
            except Exception as e:
                logger.exception("WebSocket 未预期的异常: %s", e)

            attempt += 1
