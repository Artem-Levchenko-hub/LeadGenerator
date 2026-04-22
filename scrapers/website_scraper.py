"""Качаем главную страницу компании, извлекаем чистый текст для Claude."""
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

MAX_HTML_BYTES = 1_500_000
MAX_TEXT_CHARS = 6000


@dataclass
class SiteSnapshot:
    url: str
    final_url: str
    status_code: int
    has_https: bool
    title: str | None
    description: str | None
    text_excerpt: str
    generator: str | None
    detected_stack: list[str]
    is_probably_alive: bool
    error: str | None = None


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        return urlunparse(parsed._replace(fragment=""))
    except Exception:
        return url


def _detect_stack(soup: BeautifulSoup, html: str) -> tuple[str | None, list[str]]:
    stack: list[str] = []
    generator = None

    gen_meta = soup.find("meta", attrs={"name": re.compile(r"generator", re.I)})
    if gen_meta and gen_meta.get("content"):
        generator = gen_meta["content"]
        stack.append(generator)

    lower = html.lower()
    signals = {
        "WordPress": ["wp-content/", "wp-includes/", "/wp-json/"],
        "Joomla": ["/media/jui/", "joomla"],
        "Bitrix": ["bitrix/js/", "bitrix/templates", "/bitrix/"],
        "Tilda": ["tildacdn.com", "t-records", "tilda.cc"],
        "Wix": ["static.wixstatic.com", "wix.com"],
        "Shopify": ["cdn.shopify.com", "shopify-section"],
        "Drupal": ["sites/default/files", "drupal.js"],
        "Next.js": ["_next/static/", "__next_data__"],
        "React": ["data-reactroot", "react-dom"],
        "Vue": ["vue.js", "v-app"],
        "Битрикс24 сайты": ["b24-"],
        "Nuxt.js": ["__nuxt", "_nuxt/"],
        "Gatsby": ["gatsby-image", "___gatsby"],
    }
    for name, markers in signals.items():
        if any(m in lower for m in markers):
            stack.append(name)

    return generator, list(dict.fromkeys(stack))


def _clean_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "nav", "footer"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text[:MAX_TEXT_CHARS]


def fetch_site(url: str, timeout: float = 10.0) -> SiteSnapshot:
    normalized = normalize_url(url)
    if not normalized:
        return SiteSnapshot(
            url=url, final_url="", status_code=0, has_https=False,
            title=None, description=None, text_excerpt="",
            generator=None, detected_stack=[], is_probably_alive=False,
            error="empty url",
        )

    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.9"},
            follow_redirects=True,
            timeout=timeout,
            verify=False,
        ) as client:
            resp = client.get(normalized)
            content = resp.content[:MAX_HTML_BYTES]
            html = content.decode(resp.encoding or "utf-8", errors="ignore")
    except httpx.HTTPError as e:
        return SiteSnapshot(
            url=normalized, final_url="", status_code=0, has_https=normalized.startswith("https://"),
            title=None, description=None, text_excerpt="",
            generator=None, detected_stack=[], is_probably_alive=False,
            error=str(e)[:200],
        )

    final_url = str(resp.url)
    has_https = final_url.startswith("https://")
    is_alive = 200 <= resp.status_code < 400

    if not is_alive:
        return SiteSnapshot(
            url=normalized, final_url=final_url, status_code=resp.status_code,
            has_https=has_https, title=None, description=None, text_excerpt="",
            generator=None, detected_stack=[], is_probably_alive=False,
            error=f"status {resp.status_code}",
        )

    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    desc_meta = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
    description = desc_meta.get("content", "").strip() if desc_meta else None

    generator, stack = _detect_stack(soup, html)
    text = _clean_text(soup)

    return SiteSnapshot(
        url=normalized, final_url=final_url, status_code=resp.status_code,
        has_https=has_https, title=title, description=description,
        text_excerpt=text, generator=generator, detected_stack=stack,
        is_probably_alive=True, error=None,
    )
