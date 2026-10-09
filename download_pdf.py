#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小规模文献下载 CLI：DOI -> Unpaywall/OpenAlex（合法 OA PDF）优先，再退 Sci-Hub。

实现只有一份，在 ``backend/app/services/content/pdf_downloader.py``；
本文件只是薄包装，保留原有命令行接口与用法。**改下载策略请改那边** ——
这里过去有一份四通道的完整副本，两份各自演化（"某镜像被劫持重定向"、"某镜像存储端要
TLS 指纹" 这类时效性知识尤其容易只改一边），2026-10 合并为一处实现。

用法:
    python download_pdf.py 10.1038/ng.123
    python download_pdf.py -f dois.txt -o 文献/
    cat dois.txt | python download_pdf.py
    python download_pdf.py --scihub-only -f dois.txt   # 只走 Sci-Hub
    python download_pdf.py --browser -f dois.txt       # 允许浏览器腿过验证码

依赖: pip install requests
"""

import argparse
import re
import sys
import time
import urllib3
from pathlib import Path

# 让本脚本在仓库内外任意位置运行都能找到 backend 包
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.services.content.pdf_downloader import download_pdf  # noqa: E402


def safe_name(doi: str) -> str:
    """DOI -> 落盘文件名（与旧版规则一致：非字母数字与点、横线、下划线的字符替换成 _）。"""
    return re.sub(r"[^\w.\-]+", "_", doi)[:80] + ".pdf"


def _clean_line(value: str) -> str:
    """剥掉 BOM 与首尾空白。

    必须显式剥 BOM：PowerShell 的管道会带 UTF-8 BOM（`Get-Content x | python script`
    读到的是 "\\ufeff..."），而 ``str.strip()`` **不**把 BOM 当空白 —— 不处理的话
    ``"\\ufeff"`` 会被当成一个 DOI，真的朝镜像发一次请求。
    """
    return value.lstrip("\ufeff").strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="小规模 DOI -> PDF 下载")
    ap.add_argument("dois", nargs="*", help="DOI,可多个")
    ap.add_argument("-f", "--file", help="DOI 列表文件,每行一个")
    ap.add_argument("-o", "--outdir", default="文献", help="输出目录(默认 文献/)")
    ap.add_argument("--delay", type=float, default=3.0, help="请求间隔秒数(默认 3)")
    ap.add_argument("--scihub-only", action="store_true",
                    help="跳过 Unpaywall/OpenAlex,只走 Sci-Hub")
    ap.add_argument("--insecure", action="store_true", help="忽略 SSL 证书校验(某些镜像需要)")
    ap.add_argument("--proxy", help="HTTP 代理,如 http://127.0.0.1:7890(挂梯子时用)")
    ap.add_argument("--browser", action="store_true",
                    help="Sci-Hub 直连失败时,用真实浏览器(Kimi WebBridge)过验证码后下载")
    ap.add_argument("--email", default="your@email.com",
                    help="Unpaywall 必填邮箱(默认是占位符,建议填真实邮箱)")
    ap.add_argument("--webbridge", default="http://127.0.0.1:10086",
                    help="Kimi WebBridge daemon 地址")
    args = ap.parse_args()

    dois = list(args.dois)
    if args.file:
        # utf-8-sig：记事本存过的 DOI 列表常带 BOM，不剥会把 BOM 当成一篇文献
        dois += Path(args.file).read_text(encoding="utf-8-sig").splitlines()
    if not dois and not sys.stdin.isatty():
        dois += list(sys.stdin)
    # 去空、去重、保序（DOI 本身的清洗由 download_pdf 内部完成）
    dois = list(dict.fromkeys(d for d in (_clean_line(x) for x in dois) if d))
    if not dois:
        ap.error("没有输入 DOI")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    ok_count = 0
    for i, doi in enumerate(dois, 1):
        print(f"[{i}/{len(dois)}] {doi}")
        res = download_pdf(
            doi,
            outdir / safe_name(doi),
            email=args.email,
            proxy=args.proxy or "",
            use_browser=args.browser,
            scihub_enabled=True,
            delay=args.delay,
            skip_legal=args.scihub_only,
            verify_tls=not args.insecure,
            webbridge_url=args.webbridge,
        )
        if res.ok:
            ok_count += 1
            print(f"   ✓ {res.channel}: {res.path}")
        else:
            print(f"   ✗ {res.reason}", file=sys.stderr)
        if i < len(dois):
            time.sleep(args.delay)  # 礼貌间隔，压低镜像限流

    print(f"完成: {ok_count}/{len(dois)} 成功")
    return 0 if ok_count else 1


if __name__ == "__main__":
    sys.exit(main())
