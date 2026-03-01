"""
信号生成逻辑

三根 K 线：Kn（当前未完成）、K-1（最近已完成）、K-2（再前一根已完成）

平多信号：当前价格 <= Kn.low  且  当前价格 < min(K-1.low,  K-2.low)
平空信号：当前价格 >= Kn.high 且  当前价格 > max(K-1.high, K-2.high)

设计意图：
  条件一（<= Kn.low）确保当前tick正在创下成形K线的新低，
  过滤掉"带下影线后价格已回升"的情况——此时 close > Kn.low，条件不成立，不平仓。
  条件二（< prev2_low）确保这个新低真正突破了前两根K线的支撑。
  两个条件同时满足才触发平多，平空逻辑对称。
"""

import logging
from typing import Optional
from kline_manager import KlineManager

logger = logging.getLogger(__name__)

CLOSE_LONG  = "close_long"
CLOSE_SHORT = "close_short"


def check_signal(
    kline_manager: KlineManager,
    inst_id: str,
    channel: str,
    current_price: float,
) -> Optional[str]:
    """
    根据当前价格和三根K线数据生成交易信号。
    返回 CLOSE_LONG、CLOSE_SHORT，或 None（无信号）。
    """
    buf = kline_manager.get_buffer(inst_id, channel)

    if not buf.ready():
        logger.debug(
            "%s %s: K线数据不足，已有 %d/2 根完成K线",
            inst_id, channel, len(buf.completed),
        )
        return None

    data = buf.get_signal_data(current_price)
    if data is None:
        return None

    kn_low, kn_high, prev2_low, prev2_high = data

    # 平多：当前tick创下Kn新低，且该低点低于前两根K线的低点
    if current_price <= kn_low and current_price < prev2_low:
        bars = list(buf.completed)
        logger.info(
            "【平多信号】%s %s | 当前价=%.6f  Kn.low=%.6f  K-1.low=%.6f  K-2.low=%.6f",
            inst_id, channel, current_price, kn_low, bars[1].low, bars[0].low,
        )
        return CLOSE_LONG

    # 平空：当前tick创下Kn新高，且该高点高于前两根K线的高点
    if current_price >= kn_high and current_price > prev2_high:
        bars = list(buf.completed)
        logger.info(
            "【平空信号】%s %s | 当前价=%.6f  Kn.high=%.6f  K-1.high=%.6f  K-2.high=%.6f",
            inst_id, channel, current_price, kn_high, bars[1].high, bars[0].high,
        )
        return CLOSE_SHORT

    return None
