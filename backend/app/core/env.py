"""加载项目根目录的本地环境变量。"""

from pathlib import Path

from dotenv import load_dotenv

# 当前文件位于 backend/app/core/，向上三级即为仓库根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LOADED = False


def load_env() -> None:
    """仅加载一次根目录 .env，且不覆盖命令行中已设置的环境变量。"""
    global _LOADED

    if _LOADED:
        return

    load_dotenv(PROJECT_ROOT / ".env")
    _LOADED = True
