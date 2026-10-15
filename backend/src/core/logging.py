"""Structured logging setup using loguru."""

import os
import sys

from loguru import logger

from src.core.config import settings

# 日志目录：backend/logs（基于本文件绝对路径推导，不依赖 cwd）。
# 后端由 _serve_backend.py 将 cwd 切到 backend，但用绝对路径更稳妥。
_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "logs",
)


def setup_logging() -> None:
    """Configure loguru: console + daily-rotated file sink (30-day retention)."""
    logger.remove()
    level = "DEBUG" if settings.DEBUG else "INFO"

    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    # 控制台（始终存在）
    logger.add(sys.stderr, format=fmt, level=level, enqueue=True)

    # 文件落盘：按天轮转 + 保留 30 天 + enqueue=True（worker 多线程安全必加）。
    # 失败不影响主流程，仅退回控制台日志。
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        logger.add(
            os.path.join(_LOG_DIR, "app_{time:YYYY-MM-DD}.log"),
            format=fmt,
            level=level,
            rotation="00:00",            # 每天零点轮转
            retention="30 days",         # 保留 30 天
            encoding="utf-8",
            enqueue=True,               # 异步队列，避免多线程/worker 写日志阻塞或错乱
            backtrace=True,
            diagnose=settings.DEBUG,    # 仅 DEBUG 模式附带变量诊断，生产不泄露
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"日志文件 sink 初始化失败，仅保留控制台日志：{exc}")

    logger.info(
        f"Logging initialized | level={level} debug={settings.DEBUG} log_dir={_LOG_DIR}"
    )
