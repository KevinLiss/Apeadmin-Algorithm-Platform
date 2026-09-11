"""同步 DB 访问工具（worker 线程专用）。

背景：底座 ``src.db.SessionLocal`` 是 **async_sessionmaker**（aiosqlite/aiomysql），
而 StreamWorker / WorkerManager 运行在**独立线程**中，不能使用 async session。
本模块基于 ``settings.sync_database_url``（sqlite3 / pymysql 同步驱动）创建
同步 engine + sessionmaker，供：

- ``worker._db_insert_alarm``：告警落库
- ``worker._load_model``：模型记录读取
- ``manager.start_task``：摄像头 RTSP 地址读取

注意：
- 同步 session 短用短弃（with 块内完成读写），不跨线程共享；
- engine 进程级单例，SQLite 默认 ``check_same_thread=False``（各线程独立连接）。
"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@lru_cache(maxsize=1)
def _get_sync_engine():
    """进程级同步 engine 单例。"""
    from src.core.config import settings

    url = settings.sync_database_url
    kwargs: dict = {"echo": False, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        # 多线程 worker：关闭同线程校验；绝对路径化（相对路径基于 cwd）
        kwargs["connect_args"] = {"check_same_thread": False}
        if "///" in url:
            from src.plugins.builtin.ai_vision.paths import BACKEND_ROOT

            db_file = BACKEND_ROOT / f"{settings.DB_NAME}.db"
            if db_file.exists():
                url = f"sqlite:///{db_file}"
    return create_engine(url, **kwargs)


@lru_cache(maxsize=1)
def get_sync_sessionmaker() -> sessionmaker:
    """进程级同步 sessionmaker 单例。"""
    return sessionmaker(
        bind=_get_sync_engine(),
        expire_on_commit=False,
        autoflush=False,
    )


def sync_session():
    """返回一个同步 Session 上下文管理器（worker 线程内使用）。

    用法::

        from src.plugins.builtin.ai_vision.runtime.syncdb import sync_session

        with sync_session() as db:
            db.add(record)
            db.commit()
    """
    return get_sync_sessionmaker()()
