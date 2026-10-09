"""测试夹具：隔离的 in-memory SQLite + 关闭外网测试开关。

RUN_NET=0 时跳过需要外网的集成测试（pytest.mark.integration）。
"""
from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_NET", "1") == "0":
        skip = pytest.mark.skip(reason="RUN_NET=0，跳过外网集成测试")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def test_app():
    """返回 FastAPI app（使用 in-memory SQLite，隔离测试数据）。"""
    import app.db as dbmod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    dbmod.engine = engine
    dbmod.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # 必须先导入全部模型再 create_all：create_all 只建 metadata 里已注册的表，
    # 而 models.evaluation 过去是靠 `from app.main import app`（下面第 37 行）间接触发的——
    # 那发生在 create_all 之后，于是建不建 evaluations 表取决于哪个测试模块先被收集，
    # 同一份代码换个 -k 顺序就可能报 "no such table: evaluations"。
    import app.models.evaluation  # noqa: F401
    import app.models.paper  # noqa: F401
    import app.models.project  # noqa: F401

    dbmod.Base.metadata.create_all(bind=engine)

    from app.main import app

    return app


@pytest.fixture
def client(test_app):
    from fastapi.testclient import TestClient

    with TestClient(test_app) as c:
        yield c


@pytest.fixture
def db_session():
    import app.db as dbmod

    s = dbmod.SessionLocal()
    try:
        yield s
    finally:
        s.close()
