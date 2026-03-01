#!/bin/bash
# OKX 止损止盈机器人 —— Ubuntu 启动脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 检查 Python 版本 ──────────────────────────────────────────────────────── #
python3 --version >/dev/null 2>&1 || { echo "需要 Python 3.10+"; exit 1; }

# ── 创建虚拟环境（首次运行）────────────────────────────────────────────────── #
if [ ! -d ".venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# ── 安装/更新依赖 ─────────────────────────────────────────────────────────── #
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# ── 检查配置文件 ──────────────────────────────────────────────────────────── #
if [ ! -f ".env" ]; then
    echo "错误: 未找到 .env 文件，请先复制 .env.example 并填写 API 密钥"
    echo "  cp .env.example .env"
    exit 1
fi

# ── 启动程序 ──────────────────────────────────────────────────────────────── #
echo "启动 OKX 止损止盈机器人..."
python3 main.py
