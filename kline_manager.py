"""
K 线缓冲区管理

三根 K 线定义：
  - Kn   = 当前未完成 K 线（实时成形）
  - K-1  = 最近一根已完成 K 线
  - K-2  = 再前一根已完成 K 线

平多信号：当前价格 <= Kn.low  且  当前价格 < min(K-1.low,  K-2.low)
平空信号：当前价格 >= Kn.high 且  当前价格 > max(K-1.high, K-2.high)

条件说明：
  "price <= Kn.low" 等价于"当前tick正在创下成形K线的新低"（WebSocket中close==low时）。
  分开判断 Kn 与前两根，可避免带长影线K线内价格短暂越界后回归时误触发。
"""

from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict, Tuple, Deque


@dataclass
class Kline:
    timestamp: int   # 开盘时间 (ms)
    open: float
    high: float
    low: float
    close: float
    volume: float
    completed: bool  # True = 已完成，False = 正在形成


class KlineBuffer:
    """
    维护某个 (合约, 周期) 的 K 线滚动窗口。
    completed: 最近 2 根已完成 K 线的 deque（K-2 在前，K-1 在后）
    current:   当前正在形成的 K 线 Kn（实时更新）
    """

    def __init__(self):
        # 只需保存 K-2 和 K-1 两根已完成 K 线
        self.completed: Deque[Kline] = deque(maxlen=2)
        self.current: Optional[Kline] = None

    def update(self, raw: list) -> float:
        """
        从 WebSocket 推送的原始数据更新缓冲区。
        raw 格式: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
        返回当前价格（close）。
        """
        kline = Kline(
            timestamp=int(raw[0]),
            open=float(raw[1]),
            high=float(raw[2]),
            low=float(raw[3]),
            close=float(raw[4]),
            volume=float(raw[5]),
            completed=(raw[8] == "1"),
        )

        if kline.completed:
            # 已完成 → 存入历史缓冲区，清空成形K线
            self.completed.append(kline)
            self.current = None
        else:
            # 正在成形 → 更新 Kn
            self.current = kline

        return kline.close

    def ready(self) -> bool:
        """是否已有 K-1 和 K-2 两根已完成 K 线。"""
        return len(self.completed) >= 2

    def get_signal_data(self, current_price: float) -> Optional[Tuple[float, float, float, float]]:
        """
        返回信号判断所需的四个值：(kn_low, kn_high, prev2_low, prev2_high)
          kn_low  / kn_high  : 当前成形K线 Kn 的最低/最高价
          prev2_low / prev2_high : 前两根已完成K线（K-1、K-2）的最低/最高价
        数据不足则返回 None。
        """
        if not self.ready():
            return None

        bars = list(self.completed)
        k2 = bars[0]   # K-2：较早的已完成K线
        k1 = bars[1]   # K-1：最近的已完成K线

        prev2_low  = min(k1.low,  k2.low)
        prev2_high = max(k1.high, k2.high)

        # Kn：当前成形K线；若新K线尚未开始，以当前价格作为临时参考点
        if self.current is not None:
            kn_low  = self.current.low
            kn_high = self.current.high
        else:
            kn_low = kn_high = current_price

        return kn_low, kn_high, prev2_low, prev2_high


class KlineManager:
    """管理所有 (合约, 频道) 组合的 K 线缓冲区。"""

    def __init__(self):
        self._buffers: Dict[Tuple[str, str], KlineBuffer] = {}

    def get_buffer(self, inst_id: str, channel: str) -> KlineBuffer:
        key = (inst_id, channel)
        if key not in self._buffers:
            self._buffers[key] = KlineBuffer()
        return self._buffers[key]

    def update(self, inst_id: str, channel: str, raw: list) -> float:
        """更新 K 线数据，返回当前价格。"""
        return self.get_buffer(inst_id, channel).update(raw)

    def is_ready(self, inst_id: str, channel: str) -> bool:
        return self.get_buffer(inst_id, channel).ready()
