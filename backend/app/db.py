"""SQLAlchemy 引擎与会话管理。MVP 用 SQLite；后续切 PostgreSQL 只需改连接串。"""
from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

from app.config import get_settings

settings = get_settings()
settings.ensure_dirs()

# SQLite 注意：Windows 路径 + check_same_thread=False（FastAPI 多线程访问）
engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表（幂等）+ 轻量 schema 迁移。"""
    # 导入模型触发注册
    from app.models import evaluation, paper, project  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_schema()


def _ensure_schema() -> None:
    """SQLite 轻量迁移：create_all 不会给已存在的表加列。

    列清单不再手工登记（原先 9 条 ``_ensure_column``，漏掉一条就是运行期
    "no such column"），改为拿模型 metadata 与实际 ``PRAGMA table_info`` 比对，
    差集自动 ALTER —— 新增列只要写进 ``models/``，迁移自动跟上。

    约束不在这里补：SQLite 的 ``ADD COLUMN`` 不接受没有 server_default 的 ``NOT NULL``，
    所以迁移只保证"列存在"，列约束以 ``create_all`` 建的表为准。
    """
    from app.models import evaluation, paper, project  # noqa: F401

    dialect = engine.dialect
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table.name})"))}
            if not existing:  # 表还没建出来（create_all 之后不该发生）
                continue
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = column.type.compile(dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl}"))
                conn.commit()
                logger.info("迁移: %s.%s 新增列 (%s)", table.name, column.name, ddl)
