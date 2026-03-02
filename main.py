"""
OKX 合约止损止盈机器人

策略：
  对每个配置的合约，订阅指定周期的 K 线行情。
  维护 Kn（成形中）+ K-1、K-2（已完成）共三根 K 线。

  平多信号：当前价格 <= Kn.low  且  当前价格 < min(K-1.low,  K-2.low)
  平空信号：当前价格 >= Kn.high 且  当前价格 > max(K-1.high, K-2.high)

运行：
  python main.py
"""

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import load_config, AppConfig, ContractConfig
from kline_manager import KlineManager
from strategy import check_signal, CLOSE_LONG, CLOSE_SHORT
from okx_rest import OKXRestClient
from ws_client import OKXWebSocketClient
from notifier import TelegramNotifier

# ──────────────────────────────────────────────────────────────────────────── #
#  日志配置（时间戳使用 UTC+8）                                                  #
# ──────────────────────────────────────────────────────────────────────────── #

_TZ_CST = timezone(timedelta(hours=8))


class _CSTFormatter(logging.Formatter):
    """将日志时间戳转换为 UTC+8 (CST) 显示。"""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=_TZ_CST)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def _make_handler(handler: logging.Handler) -> logging.Handler:
    handler.setFormatter(_CSTFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    return handler


logging.basicConfig(level=logging.INFO, handlers=[])   # 清空默认 handler
_root = logging.getLogger()
_root.setLevel(logging.INFO)
_root.addHandler(_make_handler(logging.StreamHandler(sys.stdout)))
_root.addHandler(_make_handler(logging.FileHandler("bot.log", encoding="utf-8")))

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────── #
#  全局状态：防止重复平仓                                                        #
# ──────────────────────────────────────────────────────────────────────────── #

# key: (inst_id, signal_type)
_in_flight: set = set()

# 冷却计时：同一信号触发后 N 秒内不再重复调用，避免频率限制
_signal_cooldown: dict = {}   # key: (inst_id, signal) -> monotonic time
_COOLDOWN_SECS = 30


# ──────────────────────────────────────────────────────────────────────────── #
#  K 线初始化（启动时 / 重连后拉取历史数据填充缓冲区）                              #
# ──────────────────────────────────────────────────────────────────────────── #

async def init_kline_buffers(
    config: AppConfig,
    rest_client: OKXRestClient,
    kline_manager: KlineManager,
):
    """
    通过 REST API 拉取历史 K 线，填充每个合约的缓冲区。
    需要至少 2 根已完成 K 线（K-1、K-2），共拉取 5 根以确保获得足够的已完成K线。
    """
    for contract in config.contracts:
        logger.info(
            "初始化 K 线缓冲区: %s %s ...",
            contract.inst_id, contract.timeframe,
        )
        candles = await rest_client.get_candles(contract.inst_id, contract.bar, limit=5)
        if not candles:
            logger.warning("  无法获取历史K线: %s", contract.inst_id)
            continue

        loaded = 0
        for raw in candles:
            # 只导入已完成的K线到缓冲区
            if raw[8] == "1":
                kline_manager.update(contract.inst_id, contract.channel, raw)
                loaded += 1

        buf = kline_manager.get_buffer(contract.inst_id, contract.channel)
        logger.info(
            "  已加载 %d 根完成K线，缓冲区 %d/2 根",
            loaded, len(buf.completed),
        )


# ──────────────────────────────────────────────────────────────────────────── #
#  平仓执行                                                                     #
# ──────────────────────────────────────────────────────────────────────────── #

async def execute_close(
    rest_client: OKXRestClient,
    inst_id: str,
    signal: str,
    pos_side_mode: str,
    notifier: "TelegramNotifier",
):
    """
    查询持仓，若持有对应方向头寸则执行平仓，并发送通知。
    """
    positions = await rest_client.get_positions(inst_id)
    if not positions:
        logger.debug("无持仓: %s", inst_id)
        return

    for pos in positions:
        pos_side  = pos.get("posSide", "net")
        avail_pos = float(pos.get("availPos", 0) or 0)
        mgn_mode  = pos.get("mgnMode", "cross")
        pos_size  = float(pos.get("pos", 0) or 0)

        if avail_pos == 0:
            continue

        should_close = False

        if signal == CLOSE_LONG:
            # 双向模式：持有多单；单向模式：净多
            if pos_side == "long" or (pos_side == "net" and pos_size > 0):
                should_close = True

        elif signal == CLOSE_SHORT:
            # 双向模式：持有空单；单向模式：净空
            if pos_side == "short" or (pos_side == "net" and pos_size < 0):
                should_close = True

        if should_close:
            direction = "多单" if signal == CLOSE_LONG else "空单"
            logger.info(
                "执行平仓: %s %s（%s 模式，可用=%s 手）",
                inst_id, direction, mgn_mode, avail_pos,
            )
            ok = await rest_client.close_position(inst_id, mgn_mode, pos_side)
            if ok:
                now = datetime.now(tz=_TZ_CST).strftime("%Y-%m-%d %H:%M:%S")
                msg = (
                    f"🔔 <b>平仓通知</b>\n"
                    f"合约：{inst_id}\n"
                    f"方向：{direction}\n"
                    f"可用：{avail_pos} 手\n"
                    f"时间：{now}"
                )
                await notifier.send(msg)


# ──────────────────────────────────────────────────────────────────────────── #
#  K 线回调                                                                     #
# ──────────────────────────────────────────────────────────────────────────── #

def make_candle_handler(
    kline_manager: KlineManager,
    rest_client: OKXRestClient,
    pos_side_mode: str,
    notifier: "TelegramNotifier",
):
    """
    工厂函数，返回 WebSocket 推送 K 线数据时的回调协程。
    """

    async def on_candle(channel: str, inst_id: str, raw: list):
        # K 线完成时记录一次 OHLC
        if raw[8] == "1":
            bar_ts = datetime.fromtimestamp(int(raw[0]) / 1000, tz=_TZ_CST)
            logger.info(
                "K线完成 %s %s | %s  O=%s H=%s L=%s C=%s",
                inst_id, channel,
                bar_ts.strftime("%Y-%m-%d %H:%M"),
                raw[1], raw[2], raw[3], raw[4],
            )

        # 更新缓冲区，获取当前价格
        current_price = kline_manager.update(inst_id, channel, raw)

        # 生成信号
        signal = check_signal(kline_manager, inst_id, channel, current_price)
        if signal is None:
            return

        # 防重入：同一合约同一信号正在处理中则跳过
        key = (inst_id, signal)
        if key in _in_flight:
            return

        # 冷却检查：上次触发后 30 秒内不重复
        now = time.monotonic()
        if now - _signal_cooldown.get(key, 0) < _COOLDOWN_SECS:
            return

        _in_flight.add(key)
        _signal_cooldown[key] = now

        try:
            await execute_close(rest_client, inst_id, signal, pos_side_mode, notifier)
        except Exception as e:
            logger.exception("平仓异常 %s %s: %s", inst_id, signal, e)
        finally:
            _in_flight.discard(key)

    return on_candle


# ──────────────────────────────────────────────────────────────────────────── #
#  主程序                                                                       #
# ──────────────────────────────────────────────────────────────────────────── #

async def main():
    logger.info("============================================================")
    logger.info("OKX 止损止盈机器人 启动中...")
    logger.info("============================================================")

    config = load_config()
    mode = "【模拟盘】" if config.demo else "【实盘】"
    logger.info("%s 持仓模式: %s", mode, config.pos_side_mode)

    for c in config.contracts:
        logger.info("  监控合约: %-20s 周期: %s", c.inst_id, c.timeframe)

    rest_client   = OKXRestClient(
        config.api_key, config.secret_key, config.passphrase, config.demo,
    )
    kline_manager = KlineManager()
    notifier      = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)

    if notifier.enabled:
        logger.info("Telegram 通知已启用（chat_id=%s）", config.telegram_chat_id)
    else:
        logger.info("Telegram 通知未配置，跳过通知")

    # 初始化历史K线缓冲区
    await init_kline_buffers(config, rest_client, kline_manager)

    # 构建 WebSocket 客户端
    async def on_reconnect():
        logger.info("WebSocket 重连，重新初始化K线缓冲区...")
        # 清空旧缓冲区
        kline_manager._buffers.clear()
        await init_kline_buffers(config, rest_client, kline_manager)

    ws_client = OKXWebSocketClient(
        on_candle=make_candle_handler(kline_manager, rest_client, config.pos_side_mode, notifier),
        on_reconnect=on_reconnect,
    )

    for contract in config.contracts:
        ws_client.add_subscription(contract.channel, contract.inst_id)

    logger.info("开始订阅行情，等待信号...")
    await ws_client.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("收到退出信号，程序停止。")
    except Exception:
        logger.exception("未捕获的异常，程序退出：")
        sys.exit(1)
