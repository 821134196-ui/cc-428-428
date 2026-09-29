"""全局配置：所有运行数据（SQLite 数据库、盘点附件）都放在本地数据目录。"""
import os
from pathlib import Path

HOME = Path(os.environ.get("ARCHIVE_HOME", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = HOME / "archive.db"
ATTACH_DIR = HOME / "attachments"

# 服务监听
HOST = os.environ.get("ARCHIVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("ARCHIVE_PORT", "8000"))

# 每盒建议容量（盒内档号必须连续）
BOX_CAPACITY = 10

# 进程内提醒扫描间隔（秒）
REMINDER_INTERVAL = int(os.environ.get("ARCHIVE_REMINDER_INTERVAL", "20"))
# 借阅到期前多少天生成提醒
DUE_SOON_DAYS = 3


def ensure_dirs():
    HOME.mkdir(parents=True, exist_ok=True)
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
