#!/bin/bash
# 档案装盒编目与库位盘点系统 — 一键启动命令
#
# 用法:
#   ./start.sh          # 初始化样例数据并启动服务器
#   ./start.sh --reset  # 重置数据库后启动
#
# 启动后访问: http://localhost:8080

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3，请先安装 Python 3.7+"
    exit 1
fi

# 检查是否需要重置
if [ "$1" == "--reset" ]; then
    echo "[启动] 重置数据库..."
    python3 seed.py
elif [ ! -f "data/archive.db" ]; then
    echo "[启动] 数据库不存在，正在初始化样例数据..."
    python3 seed.py
else
    echo "[启动] 使用现有数据库"
fi

# 启动服务器
echo "[启动] 正在启动服务器..."
exec python3 server.py
