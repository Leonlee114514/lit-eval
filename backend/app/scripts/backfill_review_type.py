"""一次性回填：对已入库论文按标题启发式修正 work_type（综述升格）。

背景：综述检测增强（2026-08-14）接入 fetch 路径，但已入库论文不会自动重抓，
"Waterborne polyurethanes: A review" 等仍标着 OpenAlex 误标的 article，综述被引
打折（citation.review_discount）不触发。本脚本扫描全部论文，用
`review_detection.detect_review` 升格 work_type 为 review（仅升格不降级）。

用法：`python -m app.scripts.backfill_review_type`（只改 work_type，不重评估；
改完可对受影响论文触发 re-evaluate 使打分生效）
"""
from __future__ import annotations

from app.db import SessionLocal
# 依次导入全部模型，让 SQLAlchemy registry 解析 Paper→Project/Evaluation 关系
from app.models.evaluation import Evaluation  # noqa: F401
from app.models.paper import Paper
from app.models.project import Project  # noqa: F401
from app.services.review_detection import detect_review


def main() -> None:
    db = SessionLocal()
    try:
        papers = db.query(Paper).all()
        changed = []
        for p in papers:
            new = detect_review(p.work_type, p.title)
            if new != p.work_type:
                changed.append((p.id, p.title, p.work_type, new))
                p.work_type = new
        db.commit()
        print(f"共 {len(papers)} 篇，修正 {len(changed)} 篇：")
        for pid, title, old, new in changed:
            print(f"  {pid} | {old!r} → {new!r} | {(title or '')[:60]}")
        if not changed:
            print("无变化。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
