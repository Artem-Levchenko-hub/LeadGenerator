"""Deploys portfolio assets to /home/i48ptgvnis/site:
1. Uploads 7 SVG covers to public/works/
2. Replaces storage/works.json with our 7 real projects
3. Triggers pm2 reload (graceful)
"""
import json
import os
import sys
from pathlib import Path

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VPS_HOST = "170.168.72.200"
VPS_USER = "i48ptgvnis"
SITE_ROOT = "/home/i48ptgvnis/site"

COVERS_DIR = Path(r"C:\c\Users\12C2~1\AppData\Local\Temp\covers")

# === Real projects ===
WORKS = [
    {
        "id": "kanavto",
        "slug": "kanavto-premium-auto",
        "title": "Kanavto — премиум-автосервис",
        "excerpt": "Полноценный продукт для автосервиса в Краснодаре: маркетинг, booking-wizard, ЛК, admin, ЮKassa, PWA.",
        "description": "Премиум-автосервис в Краснодаре, специализирующийся на BMW / Mercedes / Audi / Porsche / Skoda / VW. Полный продукт за 5 этапов: маркетинговый лендинг → 6-step booking wizard с VIN-декодером → личный кабинет с напоминаниями ТО → admin CRUD с ролевой моделью (USER/MASTER/ADMIN) → PDF заказ-наряды + ЮKassa-депозиты + SMS-реминды + A/B тестирование Hero + PWA.\n\nСтек: Next.js 15 (App Router, RSC), TypeScript, Tailwind 3, shadcn/ui, PostgreSQL + Prisma, NextAuth 5 (phone OTP + Yandex OAuth), Framer Motion, Resend, SMSC.ru, ЮKassa, Yandex.Metrica.\n\nДизайн: графитовая палитра + красный акцент, Bodoni Moda + Jost + JetBrains Mono. Motion-токены 100/150/250/400/600ms с prefers-reduced-motion cap.",
        "category": "веб-приложение",
        "tags": ["next.js", "postgres", "юkassa", "pwa", "framer-motion", "phone-otp"],
        "coverUrl": "/works/kanavto.svg",
        "images": [],
        "published": True,
        "order": 0,
        "createdAt": "2026-04-25T10:00:00Z",
    },
    {
        "id": "klining-24",
        "slug": "klining-24-recurring",
        "title": "klining-24 — клининг по подписке",
        "excerpt": "Клининг с онлайн-заказом, ЛК клиента, B2B-кабинетом, координаторской админкой и подписками.",
        "description": "Клининговый сервис нового поколения. Wizard заказа на 6 шагов, ЛК B2C с историей и оценками, отдельный B2B-кабинет (компании, договоры, инвойсы, периодичность), координаторская админка (заявки, бригады, календарь, зоны), регулярные подписки через ЮKassa.\n\n70% кода переиспользовано из Kanavto: design system, NextAuth Phone OTP, booking wizard, admin CRUD-абстракции. Полный roadmap на 6 спринтов от фундамента до B2B и cron-задач.",
        "category": "веб-приложение",
        "tags": ["next.js", "b2c", "b2b", "subscriptions", "юkassa", "telegram-bot"],
        "coverUrl": "/works/klining-24.svg",
        "images": [],
        "published": True,
        "order": 1,
        "createdAt": "2026-04-15T10:00:00Z",
    },
    {
        "id": "kuzovnsk",
        "slug": "kuzovnsk-doctor-kuzov",
        "title": "Доктор Кузов — кузовной ремонт",
        "excerpt": "Аудит существующего сайта kuzovnsk.ru + полный редизайн с калькулятором по типам кузова и wizard'ом.",
        "description": "Кузовной ремонт в Новосибирске. Сделали аудит текущего kuzovnsk.ru (UX, конверсии, технологии) и собрали полный редизайн: новый hero, калькулятор стоимости по типам кузова и видам работ (BMW, Camry, Skoda — с боковыми панелями), пошаговый wizard заказа, секция работ с before/after, FAQ.\n\nМатериалы: pitch-презентация, мокапы desktop/mobile, аудит. Готово к разработке после согласования с клиентом.",
        "category": "редизайн",
        "tags": ["audit", "redesign", "calculator", "wizard", "automotive"],
        "coverUrl": "/works/kuzovnsk.svg",
        "images": [],
        "published": True,
        "order": 2,
        "createdAt": "2026-04-20T10:00:00Z",
    },
    {
        "id": "fitness-saas",
        "slug": "fitness-saas-management",
        "title": "Fitness SaaS — управление клубом",
        "excerpt": "Платформа для фитнес-клубов: расписание, абонементы, тренеры, аналитика. С Playwright e2e и visual regression.",
        "description": "Vertical SaaS для фитнес-клубов. Менеджмент расписания (групповые и персональные), абонементы и автопродления, тренерская панель, статистика посещаемости, отчёты по выручке, push-уведомления.\n\nТехнический акцент на качество: Drizzle ORM, Playwright (e2e + visual regression на критических флоу), Vitest unit-тесты, lefthook pre-commit hooks, CI canon-review. Архитектура отделяет server-actions от RSC данных.",
        "category": "saas",
        "tags": ["saas", "drizzle", "playwright", "visual-regression", "lefthook"],
        "coverUrl": "/works/fitness-saas.svg",
        "images": [],
        "published": True,
        "order": 3,
        "createdAt": "2026-04-10T10:00:00Z",
    },
    {
        "id": "lead-hunter",
        "slug": "lead-hunter-ai-agent",
        "title": "Lead Hunter — AI-агент находит клиентов",
        "excerpt": "Автоматический B2B-парсер 2GIS + Я.Карты с сайт-аудитом, скорингом 0..100 и Outreach-агентом на DeepSeek.",
        "description": "Автономная машина для лидогенерации. Каждое утро парсит компании по 50+ запросов, аудитит сайт каждой по 8 критериям (HTTPS, mobile-viewport, скорость, формы, CMS-детект, возраст по Wayback, и т.п.), скорит 0..100, делает скриншот.\n\nДальше — Outreach Agent на DeepSeek через VseGPT/OpenRouter: глубокий анализ сайта (fetch_site, dns_check, whois), запись слабых мест, drафт холодного email, Auditor (kill-switch, 152-ФЗ, opt-out, blacklist), SMTP через Postfix + OpenDKIM (полный SPF/DKIM/DMARC chain).\n\nSales Manager Agent (Sprint 4) с BANT-квалификацией. CEO Strategic Orchestrator на Opus 4.7 для еженедельного аудита и tuning промптов. White-label опция для агентств.",
        "category": "ai-saas",
        "tags": ["ai-agents", "deepseek", "outreach", "scoring", "postfix-dkim", "react-loop"],
        "coverUrl": "/works/lead-hunter.svg",
        "images": [],
        "published": True,
        "order": 4,
        "createdAt": "2026-04-29T10:00:00Z",
    },
    {
        "id": "innertalk",
        "slug": "innertalk-corp-messenger",
        "title": "Innertalk — корпоративный мессенджер",
        "excerpt": "Замена Slack/Zoom для команд: групповые звонки, видео, чаты, файлы, поиск по треду.",
        "description": "B2B SaaS для команд, которым нужно уйти от иностранных мессенджеров. Корпоративные пространства (workspaces), thread-based чаты, групповые видеозвонки на WebRTC, обмен файлами с превью, расширенный поиск по сообщениям и файлам, интеграции (календарь, задачи).\n\n90-дневный roadmap по запуску B2B-канала. Архитектура multi-tenant, отдельная инсталляция для крупных корпоративных клиентов.",
        "category": "saas",
        "tags": ["saas", "b2b", "webrtc", "multi-tenant", "messaging"],
        "coverUrl": "/works/innertalk.svg",
        "images": [],
        "published": True,
        "order": 5,
        "createdAt": "2026-04-01T10:00:00Z",
    },
    {
        "id": "progruz",
        "slug": "progruz-logistics-saas",
        "title": "Прогруз — логистика-as-a-SaaS",
        "excerpt": "Диспетчеризация перевозок: заказы, рейсы, водители, маршруты. Physics-driven анимации.",
        "description": "In-house SaaS для логистических компаний. Заказы клиентов (Order ≠ Trip — заказ может разбиваться на несколько рейсов), рейсы и водители, маршруты с точками погрузки/выгрузки, тарифы (по км / по часам / фикс), машины с атрибутами (тип, грузоподъёмность, объём), накладные (waybill).\n\nЛендинг сделан как Trojan Horse: physics-driven демо на Matter.js (груз падает в кузов и распределяется), scroll-driven секции на Framer Motion, ROI-калькулятор. Стек: Next.js 15 (App Router, Turbopack), TypeScript, Zustand, Framer Motion + Matter.js.",
        "category": "saas",
        "tags": ["saas", "logistics", "matter.js", "framer-motion", "physics", "zustand"],
        "coverUrl": "/works/progruz.svg",
        "images": [],
        "published": True,
        "order": 6,
        "createdAt": "2026-04-05T10:00:00Z",
    },
]


def main() -> int:
    pw = os.environ.get("SSH_PASS")
    if not pw:
        sys.exit("ERROR: SSH_PASS env var not set")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VPS_HOST, username=VPS_USER, password=pw, timeout=20,
                look_for_keys=False, allow_agent=False)
    sftp = ssh.open_sftp()

    # 1. Create remote works dir if missing
    remote_works_pub = f"{SITE_ROOT}/public/works"
    try:
        sftp.stat(remote_works_pub)
    except FileNotFoundError:
        ssh.exec_command(f"mkdir -p {remote_works_pub}")[2].read()

    # 2. Upload SVGs
    print("=== Uploading covers ===")
    for slug in ("kanavto", "klining-24", "kuzovnsk", "fitness-saas",
                 "lead-hunter", "innertalk", "progruz"):
        local = COVERS_DIR / f"{slug}.svg"
        if not local.exists():
            print(f"  MISSING: {local}")
            continue
        remote = f"{remote_works_pub}/{slug}.svg"
        sftp.put(str(local), remote)
        print(f"  ↑ {slug}.svg → {remote}")

    # 3. Backup old works.json + write new one
    storage_works = f"{SITE_ROOT}/storage/works.json"
    print("\n=== works.json ===")
    try:
        sftp.stat(storage_works)
        # backup
        ts = "bak.20260430"
        i, o, e = ssh.exec_command(f"cp {storage_works} {storage_works}.{ts}")
        o.read()
        print(f"  backup → {storage_works}.{ts}")
    except FileNotFoundError:
        pass

    new_json = json.dumps(WORKS, ensure_ascii=False, indent=2)
    with sftp.open(storage_works, "w") as f:
        f.write(new_json)
    print(f"  ↑ works.json ({len(new_json):,} bytes, {len(WORKS)} projects)")

    sftp.close()
    ssh.close()
    print("\nDone. Now reload pm2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
