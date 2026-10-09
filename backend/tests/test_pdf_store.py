"""PDF 落盘命名策略：按文献身份（DOI）共享同一个文件。

守护 2026-10 review 提的第 4 条：过去按 paper 行 id 命名（``{paper_id}.pdf``），
同一篇文献在不同项目里各存一份 —— 实测 ``10.1039/c6py00350h`` 存了两份 3.7 MB。
"""
from __future__ import annotations

from app.services.content import pdf_store


def test_same_doi_lands_on_the_same_file():
    """大小写与首尾空白不影响 —— 否则跨项目去重等于白做。"""
    assert pdf_store.pdf_filename("10.1000/AbC", "pid-1") == pdf_store.pdf_filename(
        "  10.1000/abc  ", "pid-2"
    )


def test_different_doi_does_not_collide():
    assert pdf_store.pdf_filename("10.1000/a", "x") != pdf_store.pdf_filename("10.1000/b", "x")


def test_no_doi_falls_back_to_paper_id():
    """没有 DOI 就没有跨项目共享的依据，退回行 id（与旧行为一致）。"""
    for empty in (None, "", "   "):
        assert pdf_store.pdf_filename(empty, "abc-123") == "abc-123.pdf"


def test_filename_is_windows_safe():
    """DOI 里带 / \\ : ( ) 等字符时，文件名仍必须合法 —— 这正是用哈希而不是消毒的原因。"""
    name = pdf_store.pdf_filename('10.1016/j.cell.2016.07.008(1):x\\y', "pid")
    assert not any(ch in name for ch in '\\/:*?"<>|')
    assert name.endswith(".pdf")
    assert len(name) == 16 + len(".pdf")


def test_hash_algorithm_is_pinned():
    """锁死算法与截断长度：改了会让磁盘上已有文件失去引用（静默变回重复落盘）。

    期望值来自 2026-10 迁移时的实测：迁移脚本为这篇文献算出的是同一个文件名。
    """
    assert pdf_store.pdf_filename("10.1039/c6py00350h", "x") == "0b1bec60a6f651d1.pdf"


def test_pdf_path_joins_directory(tmp_path):
    expected = tmp_path / pdf_store.pdf_filename("10.1000/z", "pid")
    assert pdf_store.pdf_path(tmp_path, "10.1000/z", "pid") == expected
