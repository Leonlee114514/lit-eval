"""PDF 落盘位置策略：按**文献身份（DOI）**命名，而不是按"论文行 id"。

为什么需要它：同一篇文献在不同项目里各占一行 ``paper``（DOI 唯一性只在项目内），
按行 id 命名会让同一份 PDF 在磁盘上存多份 —— 实测 ``10.1039/c6py00350h`` 确实存了 2 份。
按 DOI 命名后，多行共用同一个文件。

文件名用 DOI 的哈希，而不是消毒后的 DOI 字符串：DOI 里会出现 ``/``、``.``、``(``、``)``、
``:`` 这类 Windows 不友好字符，消毒规则一旦漏掉一种就写出非法文件名。

**指纹不受影响**：``provenance`` 只记录全文的**字节数**（``os.stat().st_size``），
与路径无关，所以把已有文件迁到新命名不会让任何评估变成 ``data_changed``。

没有 DOI 的文献（PMID-only 或人工录入）回退到行 id —— 那种情况下没有跨项目共享的依据。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# 取哈希前 16 个十六进制字符（64 bit）：库内量级下碰撞概率可忽略，文件名也短
_HASH_LEN = 16


def pdf_filename(doi: str | None, paper_id: str) -> str:
    """落盘文件名。有 DOI 用 DOI 哈希（跨项目共享），否则回退到行 id。

    DOI 大小写不敏感，所以先 lower + strip 再哈希：``10.1000/AbC`` 与 ``10.1000/abc``
    必须落到同一个文件，否则去重又白做了。
    """
    doi = (doi or "").strip()
    if doi:
        digest = hashlib.sha256(doi.lower().encode("utf-8")).hexdigest()[:_HASH_LEN]
        return f"{digest}.pdf"
    return f"{paper_id}.pdf"


def pdf_path(pdf_dir: Path, doi: str | None, paper_id: str) -> Path:
    """落盘完整路径。调用方负责先建目录。"""
    return pdf_dir / pdf_filename(doi, paper_id)
