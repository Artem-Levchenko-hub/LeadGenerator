"""Обогащение карточек 2GIS — извлечение контактов из публичной HTML-страницы.

2GIS Catalog API в free-tier не отдаёт сайт/email/телефон. Мы парсим
публичную HTML-страницу 2gis.ru/firm/{id}: SSR-state со всеми контактами
встроен в HTML как JSON. Без LLM, без браузера, на stdlib.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx


log = logging.getLogger(__name__)


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_FIRM_URL = "https://2gis.ru/firm/{firm_id}"


@dataclass
class FirmContacts:
    website: str | None = None
    phone: str | None = None
    email: str | None = None


def enrich_firm(firm_id: str, *, timeout: float = 15.0) -> FirmContacts | None:
    """Возвращает контакты или None если страница недоступна / не разобралась.

    Не бросает исключений — на сетевой/парсинг-ошибке возвращает None,
    чтобы вызывающий Hunter всё равно создал лида (просто без сайта).
    """
    if not firm_id:
        return None
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _UA},
        ) as cli:
            r = cli.get(_FIRM_URL.format(firm_id=firm_id))
        if r.status_code != 200:
            log.info("2gis firm/%s: HTTP %s", firm_id, r.status_code)
            return None
        return parse_firm_html(r.text)
    except Exception:  # noqa: BLE001
        log.exception("2gis firm/%s: enrichment failed", firm_id)
        return None


def parse_firm_html(html: str) -> FirmContacts | None:
    """Извлекает контакты из HTML-строки. Чистая функция — для unit-тестов."""
    blocks = _find_contact_groups_blocks(html)
    if not blocks:
        return None
    websites: list[str] = []
    phones: list[str] = []
    emails: list[str] = []
    for b in blocks:
        for grp in (b.get("contact_groups") or []):
            for c in (grp.get("contacts") or []):
                t = c.get("type")
                if t == "website":
                    site = (c.get("print_text") or c.get("text") or c.get("value") or "").strip()
                    site = site.split("?", 1)[0].rstrip("/")
                    if site and "link.2gis" not in site:
                        websites.append(site)
                elif t == "phone":
                    v = (c.get("value") or "").strip()
                    if v:
                        phones.append(v)
                elif t == "email":
                    v = (c.get("value") or "").strip()
                    if v:
                        emails.append(v)
    return FirmContacts(
        website=_normalize_website(websites[0]) if websites else None,
        phone=phones[0] if phones else None,
        email=emails[0] if emails else None,
    )


def _normalize_website(site: str) -> str:
    if site.startswith(("http://", "https://")):
        return site
    return f"https://{site}"


def _find_contact_groups_blocks(html: str) -> list[dict]:
    """Все JSON-объекты содержащие ключ contact_groups.

    Алгоритм: ищем `"contact_groups":[`, идём backwards до охватывающего '{'
    с балансом скобок, потом forwards до закрывающей '}', json.loads.
    """
    out: list[dict] = []
    for match in re.finditer(r'"contact_groups"\s*:\s*\[', html):
        start = match.start()
        i = _find_object_start(html, start)
        if i < 0:
            continue
        j = _find_object_end(html, i)
        if j < 0:
            continue
        try:
            obj = json.loads(html[i:j])
        except json.JSONDecodeError:
            continue
        out.append(obj)
    return out


def _find_object_start(html: str, pos: int) -> int:
    depth = 0
    i = pos
    while i >= 0:
        ch = html[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                return i
            depth -= 1
        i -= 1
    return -1


def _find_object_end(html: str, start: int) -> int:
    depth = 0
    in_str = False
    esc = False
    j = start
    while j < len(html):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return j + 1
        j += 1
    return -1
