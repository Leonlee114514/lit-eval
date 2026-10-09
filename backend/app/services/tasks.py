"""任务队列抽象：MVP 用进程内 asyncio 任务；将来可换 Celery，接口不变。

任务存内存 dict {task_id: {status, message, result}}。MVP 场景足够。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class TaskBroker:
    """Celery 扩展位：接同名接口实现即可。"""

    def submit(self, name: str, payload: dict) -> str:  # pragma: no cover
        raise NotImplementedError

    def status(self, task_id: str) -> dict:
        raise NotImplementedError


class InProcessBroker(TaskBroker):
    def __init__(self):
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def _run(self, task_id: str, fn: Callable[[], Awaitable[Any]]) -> None:
        try:
            result = await fn()
            self._tasks[task_id]["status"] = "done"
            self._tasks[task_id]["result"] = result
        except Exception as e:
            logger.exception("task %s failed", task_id)
            self._tasks[task_id]["status"] = "failed"
            self._tasks[task_id]["message"] = f"{type(e).__name__}: {e}"

    def submit_async(self, fn: Callable[[], Awaitable[Any]], name: str = "") -> str:
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {"status": "queued", "name": name, "message": "", "result": None}
        asyncio.get_event_loop().create_task(self._run(task_id, fn))
        self._tasks[task_id]["status"] = "running"
        return task_id

    def set_progress(self, task_id: str, message: str) -> None:
        """任务运行中更新进度文案（message），供前端轮询展示。"""
        task = self._tasks.get(task_id)
        if task:
            task["message"] = message

    def status(self, task_id: str) -> dict:
        task = self._tasks.get(task_id)
        if not task:
            return {"task_id": task_id, "status": "unknown", "message": "任务不存在"}
        return {
            "task_id": task_id,
            "status": task["status"],
            "message": task["message"],
            "name": task["name"],
            "result": task["result"],
        }


broker = InProcessBroker()
