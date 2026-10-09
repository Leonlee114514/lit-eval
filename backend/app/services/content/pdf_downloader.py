"""批量 PDF 下载服务：DOI -> Unpaywall/OpenAlex（合法 OA PDF）优先，再退 Sci-Hub（requests / 浏览器腿）。

由独立 CLI 工具 download_pdf.py 移植而来。全同步函数；web worker 里用 asyncio.to_thread 包裹调用，
避免阻塞事件循环。对外主入口 download_pdf()：成功时把 PDF 落到 dest 并返回路径，失败返回各通道原因。
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────────
MIRRORS = [
    "https://sci-hub.st",
    "https://sci-hub.ru",
    "https://sci-hub.se",
]
# 浏览器腿只走实测能过的镜像（sci-hub.wf 已被劫持重定向到垃圾站，勿用）
# .ru 的存储端 requests 可直下；.st 存储端要 TLS 指纹，requests 会 403，作备胎
BROWSER_MIRRORS = [
    "https://sci-hub.ru",
    "https://sci-hub.st",
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 判定"这篇没收录"
NOT_FOUND = re.compile(
    r"(article\s+(not\s+)?found|не\s+найдена|не\s+найдено|"
    r"论文[^<]{0,10}没有|没有找到这篇)", re.I
)
# 从页面里抓 PDF 真实地址（embed / iframe / 下载按钮的 <a href>）
PDF_SRC = re.compile(
    r'<embed[^>]*\bsrc="(?P<e>[^"]+)"|'
    r'<iframe[^>]*\bsrc="(?P<i>[^"]+)"|'
    r'<a[^>]*\bhref="(?P<a>[^"]*\.pdf[^"]*)"',
    re.I,
)
# Cloudflare / DDoS-Guard / 各种反爬的挑战页标志
CHALLENGE = re.compile(
    r"captcha|checking your browser|just a moment|ddos-guard|access denied", re.I
)

# Kimi WebBridge daemon（浏览器腿：控制真实浏览器过 Cloudflare/DDoS-Guard/Altcha）
WB_DAEMON = "http://127.0.0.1:10086"
WB_SESSION = "scihub"

# 点掉 Altcha"我不是机器人"复选框，并返回页面上的 PDF 直链
WB_JS = r"""(() => {
  const cb = document.querySelector('altcha-widget .altcha-checkbox input, .altcha-checkbox input');
  if (cb && !cb.checked) { cb.click(); }
  const st = document.querySelector('altcha-widget .altcha')?.getAttribute('data-state');
  const e = document.querySelector('embed');
  const f = document.querySelector('iframe');
  const a = document.querySelector('a[href$=".pdf"]');
  return JSON.stringify({state: st || null, url: location.href,
                         embed: e ? e.src : null, iframe: f ? f.src : null,
                         pdfLink: a ? a.href : null, cookies: document.cookie});
})()"""


@dataclass
class DownloadResult:
    ok: bool
    path: str | None = None      # 成功时的落盘路径
    channel: str | None = None   # unpaywall | openalex | scihub | browser(sci-hub.ru)
    reason: str | None = None    # 失败原因（各通道拼接）


def clean(doi: str) -> str:
    doi = doi.strip()
    if doi.lower().startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]
    return doi


def to_abs(url: str, base: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    return urllib.parse.urljoin(base, url)


def _make_session(proxy: str, verify_tls: bool = True) -> requests.Session:
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    if proxy:
        sess.proxies = {"http": proxy, "https": proxy}
    if not verify_tls:
        # 少数镜像的存储端证书链不完整，requests 会 403 —— CLI 的 --insecure 走这里
        sess.verify = False
    return sess


def _download_bytes(url: str, sess: requests.Session) -> bytes:
    r = sess.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def _write_pdf(content: bytes | None, dest: Path) -> bool:
    """只把真 PDF（%PDF- 开头）落盘，防 HTML 落地页写进 fulltext_path。"""
    if not content or content[:5] != b"%PDF-":
        return False
    dest.write_bytes(content)
    return True


def _finish(dest: Path, channel: str, content: bytes) -> DownloadResult:
    if _write_pdf(content, dest):
        return DownloadResult(ok=True, path=str(dest), channel=channel)
    return DownloadResult(ok=False, reason=f"{channel}: 返回非 PDF 内容")


def _try_unpaywall(sess: requests.Session, doi: str, email: str) -> tuple[bytes | None, str]:
    """合法通道：Unpaywall 返回 best OA PDF。"""
    try:
        r = sess.get(f"https://api.unpaywall.org/v2/{doi}",
                     params={"email": email}, timeout=30)
    except requests.RequestException as e:
        return None, f"请求失败: {e}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    data = r.json()
    if not isinstance(data, dict):
        return None, "非预期响应"
    pdf = (data.get("best_oa_location") or {}).get("url_for_pdf")
    if not pdf:
        return None, "无 OA PDF"
    try:
        return _download_bytes(pdf, sess), ""
    except requests.RequestException as e:
        return None, f"下载失败: {e}"


def _try_openalex(sess: requests.Session, doi: str) -> tuple[bytes | None, str]:
    """合法通道之二：OpenAlex locations[].pdf_url，补 Unpaywall 的边界情况。"""
    try:
        r = sess.get(f"https://api.openalex.org/works/doi:{doi}", timeout=30)
    except requests.RequestException as e:
        return None, f"请求失败: {e}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    data = r.json()
    if not isinstance(data, dict):
        return None, "非预期响应"
    bl = data.get("best_oa_location") or {}
    pl = data.get("primary_location") or {}
    urls = []
    for loc in data.get("locations") or []:
        if loc.get("pdf_url"):
            urls.append(loc["pdf_url"])
    if bl.get("pdf_url"):
        urls.append(bl["pdf_url"])
    if pl.get("pdf_url"):
        urls.append(pl["pdf_url"])
    oa = (data.get("open_access") or {}).get("oa_url")
    if oa:
        urls.append(oa)
    seen: set[str] = set()
    failed = 0
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        try:
            rr = sess.get(u, timeout=60)
            rr.raise_for_status()
            if rr.content[:5] != b"%PDF-":
                failed += 1
                continue
            return rr.content, ""
        except requests.RequestException:
            failed += 1
    if not urls:
        return None, "无 PDF 链接"
    return None, f"{failed} 个链接不可用"


def _try_scihub(sess: requests.Session, doi: str, delay: float) -> tuple[bytes | None, str]:
    """逐个镜像试，拿 PDF。"""
    url = urllib.parse.quote(doi, safe="/")
    for mirror in MIRRORS:
        try:
            r = sess.get(f"{mirror}/{url}", timeout=30)
        except requests.RequestException as e:
            logger.warning("[pdf] %s 请求失败: %s", mirror, e)
            continue
        if r.status_code == 403 or CHALLENGE.search(r.text):
            logger.warning("[pdf] %s 反爬验证(Cloudflare/DDoS-Guard),跳过", mirror)
            continue
        if NOT_FOUND.search(r.text):
            logger.warning("[pdf] %s 未收录该篇", mirror)
            continue
        if "application/pdf" in r.headers.get("content-type", ""):
            return r.content, ""
        m = PDF_SRC.search(r.text)
        if m:
            src = next(v for v in m.groupdict().values() if v)
            try:
                return _download_bytes(to_abs(src, mirror), sess), ""
            except requests.RequestException as e:
                logger.warning("[pdf] %s PDF 下载失败: %s", mirror, e)
                continue
        logger.warning("[pdf] %s 页面里没找到 PDF 链接", mirror)
        time.sleep(delay)
    return None, "所有镜像都失败"


# ── 浏览器腿：Kimi WebBridge 控制真实浏览器过 Cloudflare/DDoS-Guard/Altcha ──
def _wb_command(action: str, args: dict | None, wb_url: str, wb_session: str,
                timeout: int = 60) -> dict:
    r = requests.post(f"{wb_url}/command",
                      json={"action": action, "args": args or {}, "session": wb_session},
                      timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"webbridge {action} 失败: {data}")
    return data.get("data")


def browser_available(wb_url: str = WB_DAEMON, wb_session: str = WB_SESSION) -> bool:
    """探测 webbridge daemon 是否在线（batch 级预检用，不在线则整批跳过浏览器腿）。"""
    try:
        _wb_command("list_tabs", {}, wb_url, wb_session, timeout=5)
        return True
    except Exception:
        return False


def proxy_available(proxy: str, timeout: int = 8) -> bool:
    """带代理快速连通性探测（batch 级预检：Clash 没挂就整批跳过 Sci-Hub 通道）。"""
    if not proxy:
        return False
    try:
        r = requests.get(
            "https://api.openalex.org/works?per-page=1",
            proxies={"http": proxy, "https": proxy},
            timeout=timeout,
        )
        return r.status_code < 500
    except Exception:
        return False


def _wb_get_pdf(article_url: str, wb_url: str, wb_session: str) -> tuple[str | None, str | None, dict]:
    """导航到文章页，自动点 Altcha，轮询到 PDF 直链。返回 (pdf_url, 状态, cookies)。"""
    _wb_command("navigate", {"url": article_url, "newTab": True}, wb_url, wb_session)
    for _ in range(8):
        time.sleep(2.5)
        res = _wb_command("evaluate", {"code": WB_JS}, wb_url, wb_session)
        info = json.loads(res["value"])
        pdf = info.get("pdfLink") or info.get("embed") or info.get("iframe")
        if pdf and not pdf.startswith("chrome-error"):
            cookies: dict[str, str] = {}
            for kv in (info.get("cookies") or "").split(";"):
                if "=" in kv:
                    k, v = kv.strip().split("=", 1)
                    cookies[k] = v
            return pdf, info.get("state"), cookies
    return None, "超时或验证码未过", {}


def _try_browser(doi: str, sess: requests.Session, wb_url: str,
                 wb_session: str) -> tuple[bytes | None, str]:
    """requests 抓不到时，用真实浏览器过验证码后拿直链，再用 requests 下载。"""
    quoted = urllib.parse.quote(doi, safe="/")
    for mirror in BROWSER_MIRRORS:
        try:
            pdf, state, cookies = _wb_get_pdf(f"{mirror}/{quoted}", wb_url, wb_session)
            if not pdf:
                logger.warning("[pdf] browser %s: 未拿到 PDF 直链(%s)", mirror, state)
                continue
            try:
                content = _download_bytes(pdf, sess)
            except requests.RequestException:
                # 403 常因缺 Cloudflare 会话 cookie，带上浏览器 cookie 重试
                # 重建会话以带上 Cloudflare cookie；verify / headers / proxies 都要继承，
                # 否则 --insecure（verify=False）恰好在这条腿上失效
                sess2 = _make_session("", sess.verify)
                sess2.headers.update(sess.headers)
                sess2.proxies.update(sess.proxies or {})
                sess2.cookies.update(cookies)
                content = _download_bytes(pdf, sess2)
            if content and content[:5] == b"%PDF-":
                return content, f"browser({mirror.split('//')[1]})"
            logger.warning("[pdf] browser %s: 拿到但非 PDF", mirror)
        except requests.RequestException as e:
            logger.warning("[pdf] browser %s 下载失败: %s", mirror, e)
        except Exception as e:
            logger.warning("[pdf] browser %s: %s", mirror, e)
        finally:
            try:
                _wb_command("close_tab", {}, wb_url, wb_session)
            except Exception:
                pass
    return None, "browser: 全部镜像失败"


# ── 主入口 ──────────────────────────────────────────────────────────────
def download_pdf(
    doi: str,
    dest: Path,
    *,
    email: str,
    proxy: str = "",
    use_browser: bool = True,
    scihub_enabled: bool = True,
    delay: float = 3.0,
    skip_legal: bool = False,
    verify_tls: bool = True,
    webbridge_url: str = WB_DAEMON,
    webbridge_session: str = WB_SESSION,
) -> DownloadResult:
    """四通道下载：Unpaywall → OpenAlex → Sci-Hub(requests) → Sci-Hub(浏览器腿)。

    任一通道拿到 %PDF- 开头内容即落盘返回；全失败返回各通道原因拼接。

    ``skip_legal=True``   跳过前两个合法通道，只走 Sci-Hub（对应 CLI 的 ``--scihub-only``）
    ``verify_tls=False``  关闭 SSL 证书校验（对应 CLI 的 ``--insecure``，某些镜像需要）
    """
    doi = clean(doi)
    sess = _make_session(proxy, verify_tls)
    reasons: list[str] = []

    if not skip_legal:
        content, reason = _try_unpaywall(sess, doi, email)
        if content:
            return _finish(dest, "unpaywall", content)
        reasons.append(f"unpaywall: {reason}")

        content, reason = _try_openalex(sess, doi)
        if content:
            return _finish(dest, "openalex", content)
        reasons.append(f"openalex: {reason}")

    if scihub_enabled:
        content, reason = _try_scihub(sess, doi, delay)
        if content:
            return _finish(dest, "scihub", content)
        reasons.append(f"scihub: {reason}")

    if use_browser:
        content, reason = _try_browser(doi, sess, webbridge_url, webbridge_session)
        if content:
            return _finish(dest, "browser", content)
        reasons.append(f"browser: {reason}")

    return DownloadResult(ok=False, reason="; ".join(reasons) or "所有通道均为关闭状态")
