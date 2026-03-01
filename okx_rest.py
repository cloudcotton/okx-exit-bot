"""
OKX REST API 封装
  - 查询持仓
  - 平仓
  - 拉取历史 K 线（用于启动时初始化缓冲区）
"""

import aiohttp
import hashlib
import hmac
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.okx.com"


def _utc_timestamp() -> str:
    """生成 OKX 要求的时间戳格式：2020-12-08T09:08:57.715Z"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


class OKXRestClient:

    def __init__(self, api_key: str, secret_key: str, passphrase: str, demo: bool = False):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.demo = demo

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        message = timestamp + method.upper() + path + body
        mac = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            digestmod=hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _auth_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        ts = _utc_timestamp()
        headers = {
            "OK-ACCESS-KEY":        self.api_key,
            "OK-ACCESS-SIGN":       self._sign(ts, method, path, body),
            "OK-ACCESS-TIMESTAMP":  ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type":         "application/json",
        }
        if self.demo:
            headers["x-simulated-trading"] = "1"
        return headers

    # ------------------------------------------------------------------ #
    #  持仓查询                                                            #
    # ------------------------------------------------------------------ #

    async def get_positions(self, inst_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        查询当前持仓。
        返回持仓列表，若请求失败则返回空列表。
        """
        path = "/api/v5/account/positions"
        if inst_id:
            path += f"?instId={inst_id}"

        headers = self._auth_headers("GET", path)

        async with aiohttp.ClientSession() as session:
            async with session.get(_BASE_URL + path, headers=headers) as resp:
                data = await resp.json()

        if data.get("code") != "0":
            logger.error("get_positions 失败: %s", data)
            return []
        return data.get("data", [])

    # ------------------------------------------------------------------ #
    #  平仓                                                                #
    # ------------------------------------------------------------------ #

    async def close_position(
        self,
        inst_id: str,
        mgn_mode: str,
        pos_side: str,
        ccy: Optional[str] = None,
    ) -> bool:
        """
        平仓。
        inst_id:  合约 ID，如 "BTC-USDT-SWAP"
        mgn_mode: "cross" 或 "isolated"
        pos_side: "long"、"short" 或 "net"
        返回是否成功。
        """
        path = "/api/v5/trade/close-position"
        payload: Dict[str, Any] = {
            "instId":  inst_id,
            "mgnMode": mgn_mode,
            "posSide": pos_side,
        }
        if ccy:
            payload["ccy"] = ccy

        body = json.dumps(payload)
        headers = self._auth_headers("POST", path, body)

        async with aiohttp.ClientSession() as session:
            async with session.post(_BASE_URL + path, headers=headers, data=body) as resp:
                data = await resp.json()

        if data.get("code") != "0":
            logger.error("close_position 失败 %s %s: %s", inst_id, pos_side, data)
            return False

        logger.info("平仓成功: %s %s", inst_id, pos_side)
        return True

    # ------------------------------------------------------------------ #
    #  历史 K 线（用于初始化缓冲区）                                        #
    # ------------------------------------------------------------------ #

    async def get_candles(
        self,
        inst_id: str,
        bar: str,
        limit: int = 5,
    ) -> List[list]:
        """
        获取历史 K 线数据（无需鉴权，公开接口）。
        bar: K 线周期，如 "5m"、"15m"、"1H"、"4H"、"1D"
        返回列表（时间从旧到新），每项格式:
          [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
        """
        path = f"/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}"

        async with aiohttp.ClientSession() as session:
            async with session.get(_BASE_URL + path) as resp:
                data = await resp.json()

        if data.get("code") != "0":
            logger.error("get_candles 失败 %s %s: %s", inst_id, bar, data)
            return []

        # OKX 返回最新的在前，反转为时间正序
        candles = list(reversed(data.get("data", [])))
        return candles
