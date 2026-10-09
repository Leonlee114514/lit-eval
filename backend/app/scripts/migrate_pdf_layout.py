"""把已有 PDF 迁到"按 DOI 命名"的新布局，并处置孤儿文件。

背景：过去按 paper 行 id 命名（``{paper_id}.pdf``）。同一篇文献在不同项目里各占一行，
于是同一份 PDF 存了多份；而 paper 行被删掉后，它的 PDF 就成了没人引用的孤儿
（实测 pdfs/ 里 10 个孤儿 / 30.8 MB，占该目录 40%）。

**默认只预览（dry-run）**，确认后加 ``--apply`` 才动文件与数据库。

迁移不影响评估指纹：``provenance`` 只记全文的字节数，与路径无关 ——
所以迁移后不会有一篇文献突然变成 ``data_changed``。

用法：
    python -m app.scripts.migrate_pdf_layout              # 预览
    python -m app.scripts.migrate_pdf_layout --apply      # 执行
    python -m app.scripts.migrate_pdf_layout --apply --keep-orphans   # 跳过孤儿处置
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.models import evaluation, paper, project  # noqa: F401  注册全部模型
from app.models.paper import Paper
from app.services.content import pdf_store


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_plan(session, pdf_dir: Path) -> dict:
    """算出迁移计划，不碰任何文件。"""
    groups: dict[Path, list[tuple]] = defaultdict(list)
    missing: list[tuple] = []

    for p in session.query(Paper).all():
        if not p.fulltext_path:
            continue
        src = Path(p.fulltext_path)
        if not src.exists():
            missing.append((p, src))
            continue
        groups[pdf_store.pdf_path(pdf_dir, p.doi, p.id)].append((p, src))

    moves, merges, conflicts, already = [], [], [], []
    for dst, rows in sorted(groups.items(), key=lambda kv: str(kv[0])):
        srcs = sorted({src for _p, src in rows}, key=str)
        if len(srcs) == 1 and srcs[0].name == dst.name:
            already.append((dst, rows))
        elif len(srcs) == 1:
            moves.append((rows, srcs[0], dst))
        else:
            digests = {s: _sha256(s) for s in srcs}
            (merges if len(set(digests.values())) == 1 else conflicts).append((rows, srcs, dst))

    # 孤儿：目录里存在、但没有任何 paper 行引用、也不在任何迁移目标上的文件
    referenced = {src.name for rows in groups.values() for _p, src in rows}
    referenced |= {dst.name for dst in groups}
    orphans = sorted(
        (f for f in pdf_dir.glob("*.pdf") if f.name not in referenced),
        key=lambda f: f.stat().st_size,
        reverse=True,
    )

    return {
        "already": already, "moves": moves, "merges": merges,
        "conflicts": conflicts, "missing": missing, "orphans": orphans,
        "rows": sum(len(rows) for rows in groups.values()),
    }


def _report(plan: dict) -> None:
    print(f"涉及的 paper 行：{plan['rows']}")
    print(f"  已合规（无需动）    : {len(plan['already'])}")
    print(f"  重命名（一对一）    : {len(plan['moves'])}")
    print(f"  合并（多行内容相同）: {len(plan['merges'])}")
    print(f"  冲突（内容不同）    : {len(plan['conflicts'])}")
    print(f"  引用失效（文件不在）: {len(plan['missing'])}")
    orphans = plan["orphans"]
    size = sum(f.stat().st_size for f in orphans) / 1024 / 1024
    print(f"孤儿文件            : {len(orphans)} 个 / {size:.1f} MB")
    print()
    for rows, src, dst in plan["moves"]:
        print(f"  move   {src.name}  ->  {dst.name}   （{len(rows)} 行引用）")
    for rows, srcs, dst in plan["merges"]:
        print(f"  merge  {', '.join(s.name for s in srcs)}  ->  {dst.name}   （{len(rows)} 行）")
    for rows, srcs, dst, digests in plan["conflicts"]:
        print(f"  !! 冲突 {dst.name}: {len(srcs)} 个来源内容不同，需人工判断")
        for s in srcs:
            print(f"       {s.name}  {s.stat().st_size/1024/1024:.2f} MB  sha256={digests[s][:12]}")
    for p, src in plan["missing"]:
        print(f"  !! 引用失效: paper={p.id[:8]} doi={p.doi} path={src}")
    for f in orphans[:20]:
        print(f"  orphan {f.name}  {f.stat().st_size/1024/1024:.2f} MB")
    if len(orphans) > 20:
        print(f"  … 另有 {len(orphans) - 20} 个")


def apply_plan(session, pdf_dir: Path, plan: dict, keep_orphans: bool) -> dict:
    """执行迁移。返回统计。"""
    moved = merged = 0

    for rows, src, dst in plan["moves"]:
        shutil.move(str(src), str(dst))
        for p, _old in rows:
            p.fulltext_path = str(dst)
        moved += 1

    for rows, srcs, dst in plan["merges"]:
        shutil.move(str(srcs[0]), str(dst))
        for extra in srcs[1:]:
            extra.unlink()  # 内容相同（已比对 sha256），可以安全删
        for p, _old in rows:
            p.fulltext_path = str(dst)
        merged += 1

    backup_dir = None
    orphan_count = 0
    if not keep_orphans and plan["orphans"]:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = get_settings().data_dir / "backups" / f"orphan_pdfs_{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for f in plan["orphans"]:
            shutil.move(str(f), str(backup_dir / f.name))
            orphan_count += 1

    session.commit()
    return {"moved": moved, "merged": merged, "orphans": orphan_count, "backup_dir": backup_dir}


def main() -> int:
    ap = argparse.ArgumentParser(description="迁移 PDF 到按 DOI 命名并处置孤儿")
    ap.add_argument("--apply", action="store_true", help="真的动文件与数据库（默认只预览）")
    ap.add_argument("--keep-orphans", action="store_true", help="保留孤儿文件，不移到备份")
    args = ap.parse_args()

    pdf_dir = get_settings().data_dir / "pdfs"
    if not pdf_dir.exists():
        print(f"目录不存在: {pdf_dir}")
        return 1

    session = SessionLocal()
    try:
        plan = build_plan(session, pdf_dir)
        _report(plan)
        print()
        if not args.apply:
            print("以上是预览（未改动任何文件）。确认后重跑并加 --apply。")
            return 0

        stats = apply_plan(session, pdf_dir, plan, keep_orphans=args.keep_orphans)
        print(
            f"已执行：重命名 {stats['moved']}，合并 {stats['merged']}，"
            f"孤儿移出 {stats['orphans']}"
            + (f" → {stats['backup_dir']}" if stats["backup_dir"] else "")
        )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
