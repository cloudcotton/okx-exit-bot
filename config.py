import os
import yaml
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import List

load_dotenv()

# 用户友好的时间周期 → OKX WebSocket 频道名
TIMEFRAME_TO_BAR = {
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "60m": "1H",
    "1h":  "1H",
    "1H":  "1H",
    "4h":  "4H",
    "4H":  "4H",
    "1d":  "1D",
    "1D":  "1D",
}


def _to_channel(timeframe: str) -> str:
    bar = TIMEFRAME_TO_BAR.get(timeframe)
    if bar is None:
        raise ValueError(
            f"不支持的 K 线周期: '{timeframe}'。"
            f"支持: {list(TIMEFRAME_TO_BAR.keys())}"
        )
    return f"candle{bar}"


def _to_bar(timeframe: str) -> str:
    bar = TIMEFRAME_TO_BAR.get(timeframe)
    if bar is None:
        raise ValueError(f"不支持的 K 线周期: '{timeframe}'")
    return bar


@dataclass
class ContractConfig:
    inst_id: str
    timeframe: str
    channel: str   # e.g. "candle15m"
    bar: str       # e.g. "15m"  (用于 REST API 请求)


@dataclass
class AppConfig:
    api_key: str
    secret_key: str
    passphrase: str
    demo: bool
    contracts: List[ContractConfig]
    pos_side_mode: str


def load_config(path: str = "config.yaml") -> AppConfig:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if len(data["contracts"]) > 5:
        raise ValueError("最多支持 5 个合约")

    contracts = []
    for c in data["contracts"]:
        tf = c["timeframe"]
        contracts.append(ContractConfig(
            inst_id=c["instId"],
            timeframe=tf,
            channel=_to_channel(tf),
            bar=_to_bar(tf),
        ))

    for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"):
        if not os.environ.get(key):
            raise EnvironmentError(f"缺少环境变量: {key}，请检查 .env 文件")

    return AppConfig(
        api_key=os.environ["OKX_API_KEY"],
        secret_key=os.environ["OKX_SECRET_KEY"],
        passphrase=os.environ["OKX_PASSPHRASE"],
        demo=os.environ.get("OKX_DEMO", "false").lower() == "true",
        contracts=contracts,
        pos_side_mode=data.get("pos_side_mode", "long_short"),
    )
