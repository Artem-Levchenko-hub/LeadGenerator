"""Генерирует 7 SVG-cover'ов для портфолио в стиле innertalk-site
(cream/olive/red palette, h-display serif typography, aspect 4:5).
Записывает в /tmp/covers/ откуда мы их зальём через SFTP.
"""
from pathlib import Path

OUT = Path("/c/temp_covers")
OUT.mkdir(parents=True, exist_ok=True)

# Site palette
BG = "#FAF8F5"
SURFACE = "#FFFFFF"
FG = "#141414"
MUTED = "#5C5C5C"
OLIVE = "#8B9258"
OLIVE_SOFT = "#EFEEC8"
ACCENT2 = "#8B2F2F"  # deep red
LINE = "#E8E4DE"

W, H = 1200, 1500


def cover(slug: str, body: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" preserveAspectRatio="xMidYMid slice">
  <defs>
    <style>
      .display {{ font-family: 'Bodoni Moda', 'Playfair Display', Georgia, serif; font-weight: 600; letter-spacing: -0.025em; }}
      .display-it {{ font-family: 'Bodoni Moda', Georgia, serif; font-weight: 500; font-style: italic; letter-spacing: -0.02em; }}
      .mono {{ font-family: 'JetBrains Mono', 'IBM Plex Mono', monospace; font-weight: 400; letter-spacing: 0.18em; }}
      .body {{ font-family: 'Inter', system-ui, sans-serif; font-weight: 400; }}
    </style>
  </defs>
  {body}
</svg>'''


# ============ 1. KANAVTO — premium auto, dark-on-cream + red accent ============
kanavto = cover("kanavto", f'''
  <rect width="{W}" height="{H}" fill="{BG}"/>
  <!-- diagonal red bar slightly off-center -->
  <rect x="-100" y="380" width="1500" height="40" fill="{ACCENT2}" transform="rotate(-12 600 400)" opacity="0.92"/>
  <!-- Subtle racing stripe pattern -->
  <g opacity="0.06">
    <line x1="0" y1="0" x2="{W}" y2="{H}" stroke="{FG}" stroke-width="2"/>
    <line x1="80" y1="0" x2="{W+80}" y2="{H}" stroke="{FG}" stroke-width="1"/>
    <line x1="160" y1="0" x2="{W+160}" y2="{H}" stroke="{FG}" stroke-width="1"/>
  </g>
  <!-- Top mono label -->
  <text x="80" y="120" class="mono" font-size="22" fill="{MUTED}">KANAVTO.COM · KRASNODAR</text>
  <line x1="80" y1="150" x2="180" y2="150" stroke="{OLIVE}" stroke-width="1.5"/>
  <!-- Main display -->
  <text x="80" y="700" class="display" font-size="220" fill="{FG}">Kanavto</text>
  <text x="80" y="900" class="display-it" font-size="120" fill="{ACCENT2}">premium auto</text>
  <!-- Brand list bottom -->
  <g transform="translate(80, 1280)">
    <text class="mono" font-size="24" fill="{FG}">BMW</text>
    <text x="120" class="mono" font-size="24" fill="{FG}">MERCEDES</text>
    <text x="320" class="mono" font-size="24" fill="{FG}">AUDI</text>
    <text x="430" class="mono" font-size="24" fill="{FG}">PORSCHE</text>
    <text x="610" class="mono" font-size="24" fill="{FG}">SKODA</text>
    <text x="730" class="mono" font-size="24" fill="{FG}">VW</text>
  </g>
  <text x="80" y="1400" class="body" font-size="22" fill="{MUTED}">Booking wizard · ЛК · admin · ЮKassa · PWA · 5/5 этапов</text>
''')

# ============ 2. KLINING-24 — clean B2C+B2B, olive + sparkle ============
klining = cover("klining-24", f'''
  <rect width="{W}" height="{H}" fill="{BG}"/>
  <rect x="0" y="0" width="{W}" height="{H}" fill="{OLIVE_SOFT}" opacity="0.4"/>
  <!-- Sparkle pattern -->
  <g fill="{OLIVE}" opacity="0.5">
    <circle cx="180" cy="200" r="3"/>
    <circle cx="950" cy="280" r="4"/>
    <circle cx="380" cy="450" r="2"/>
    <circle cx="1050" cy="520" r="3"/>
    <circle cx="220" cy="780" r="3"/>
    <circle cx="900" cy="950" r="4"/>
    <circle cx="600" cy="1100" r="2"/>
  </g>
  <text x="80" y="120" class="mono" font-size="22" fill="{MUTED}">KLINING-24.RU · CLEAN AS A SERVICE</text>
  <line x1="80" y1="150" x2="180" y2="150" stroke="{OLIVE}" stroke-width="1.5"/>
  <text x="80" y="650" class="display" font-size="200" fill="{FG}">Чистота</text>
  <text x="80" y="850" class="display-it" font-size="200" fill="{OLIVE}">по подписке.</text>
  <text x="80" y="1280" class="body" font-size="32" fill="{FG}">B2C · B2B · Wizard · ЮKassa · Recurring</text>
  <text x="80" y="1340" class="mono" font-size="20" fill="{MUTED}">Координатор-админка · Бригады · Зоны · Telegram-bot</text>
''')

# ============ 3. KUZOVNSK — body shop redesign (real photo as background) ============
# uses the real screenshot we have on VPS — но fallback: make text overlay
kuzovnsk = cover("kuzovnsk", f'''
  <rect width="{W}" height="{H}" fill="{FG}"/>
  <!-- Steel-gradient background mesh -->
  <defs>
    <linearGradient id="steel" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#2B2B2B"/>
      <stop offset="100%" stop-color="#0D0D0D"/>
    </linearGradient>
    <radialGradient id="redglow" cx="80%" cy="20%" r="50%">
      <stop offset="0%" stop-color="{ACCENT2}" stop-opacity="0.4"/>
      <stop offset="100%" stop-color="{ACCENT2}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#steel)"/>
  <rect width="{W}" height="{H}" fill="url(#redglow)"/>
  <!-- Tool stripes -->
  <g opacity="0.1" stroke="white" stroke-width="1" fill="none">
    <line x1="0" y1="200" x2="{W}" y2="200"/>
    <line x1="0" y1="1300" x2="{W}" y2="1300"/>
  </g>
  <text x="80" y="120" class="mono" font-size="22" fill="#A0A0A0">KUZOVNSK.RU · НОВОСИБИРСК</text>
  <line x1="80" y1="150" x2="180" y2="150" stroke="{ACCENT2}" stroke-width="1.5"/>
  <text x="80" y="700" class="display" font-size="180" fill="white">Доктор</text>
  <text x="80" y="900" class="display-it" font-size="180" fill="{ACCENT2}">Кузов.</text>
  <text x="80" y="1280" class="body" font-size="28" fill="white">Кузовной ремонт · Покраска · Полировка</text>
  <text x="80" y="1340" class="mono" font-size="20" fill="#A0A0A0">Audit + redesign · Калькулятор · Wizard заказа</text>
''')

# ============ 4. FITNESS-SAAS — energetic peach + olive ============
fitness = cover("fitness-saas", f'''
  <rect width="{W}" height="{H}" fill="{BG}"/>
  <!-- Warm peach gradient -->
  <defs>
    <radialGradient id="peach" cx="30%" cy="40%" r="80%">
      <stop offset="0%" stop-color="#FFB088" stop-opacity="0.4"/>
      <stop offset="50%" stop-color="#F4A067" stop-opacity="0.2"/>
      <stop offset="100%" stop-color="{BG}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#peach)"/>
  <!-- Pulse rings -->
  <g fill="none" stroke="{ACCENT2}" stroke-width="1.5" opacity="0.25">
    <circle cx="950" cy="350" r="160"/>
    <circle cx="950" cy="350" r="220"/>
    <circle cx="950" cy="350" r="280"/>
  </g>
  <text x="80" y="120" class="mono" font-size="22" fill="{MUTED}">FITNESS · SAAS</text>
  <line x1="80" y1="150" x2="180" y2="150" stroke="{OLIVE}" stroke-width="1.5"/>
  <text x="80" y="650" class="display" font-size="200" fill="{FG}">Фитнес</text>
  <text x="80" y="850" class="display-it" font-size="200" fill="{ACCENT2}">как продукт.</text>
  <text x="80" y="1280" class="body" font-size="28" fill="{FG}">Расписание · Тренеры · Абонементы · Аналитика</text>
  <text x="80" y="1340" class="mono" font-size="20" fill="{MUTED}">Drizzle ORM · Playwright e2e · visual regression</text>
''')

# ============ 5. LEAD-HUNTER — data feeling, dot grid ============
lead_hunter = cover("lead-hunter", f'''
  <rect width="{W}" height="{H}" fill="{BG}"/>
  <!-- Dense data dot-grid -->
  <g fill="{OLIVE}" opacity="0.18">
    {''.join(f'<circle cx="{60+i*60}" cy="{220+j*60}" r="2"/>' for i in range(20) for j in range(20))}
  </g>
  <!-- Highlighted scoring dots -->
  <g fill="{ACCENT2}">
    <circle cx="180" cy="280" r="6"/>
    <circle cx="540" cy="400" r="6"/>
    <circle cx="900" cy="340" r="6"/>
    <circle cx="780" cy="640" r="6"/>
    <circle cx="360" cy="760" r="6"/>
    <circle cx="1020" cy="820" r="6"/>
  </g>
  <text x="80" y="120" class="mono" font-size="22" fill="{MUTED}">LEAD HUNTER + OMNIA MACHINE</text>
  <line x1="80" y1="150" x2="180" y2="150" stroke="{OLIVE}" stroke-width="1.5"/>
  <text x="80" y="950" class="display" font-size="170" fill="{FG}">AI-агент</text>
  <text x="80" y="1100" class="display-it" font-size="100" fill="{ACCENT2}">находит клиентов 24/7.</text>
  <text x="80" y="1280" class="body" font-size="22" fill="{FG}">2GIS · Я.Карты · 8-критериев аудит · DeepSeek + Claude</text>
  <text x="80" y="1330" class="mono" font-size="18" fill="{MUTED}">Postfix + DKIM/SPF/DMARC · Auditor · Sales BANT</text>
''')

# ============ 6. INNERTALK — corporate messenger, blue speech bubbles ============
innertalk = cover("innertalk", f'''
  <rect width="{W}" height="{H}" fill="{BG}"/>
  <!-- Speech-bubble pattern -->
  <g opacity="0.18" fill="none" stroke="{OLIVE}" stroke-width="2">
    <path d="M 920 220 q 0 -60 60 -60 h 100 q 60 0 60 60 v 50 q 0 60 -60 60 h -50 l -30 30 v -30 h -20 q -60 0 -60 -60 z"/>
    <path d="M 200 1080 q 0 -50 50 -50 h 120 q 50 0 50 50 v 40 q 0 50 -50 50 h -90 l -25 25 v -25 h -5 q -50 0 -50 -50 z"/>
  </g>
  <!-- More bubbles filled olive-soft -->
  <g opacity="0.6" fill="{OLIVE_SOFT}">
    <ellipse cx="1000" cy="450" rx="80" ry="25"/>
    <ellipse cx="350" cy="900" rx="100" ry="30"/>
  </g>
  <text x="80" y="120" class="mono" font-size="22" fill="{MUTED}">INNERTALK.SPACE · CORP MESSENGER</text>
  <line x1="80" y1="150" x2="180" y2="150" stroke="{OLIVE}" stroke-width="1.5"/>
  <text x="80" y="650" class="display" font-size="190" fill="{FG}">Команды.</text>
  <text x="80" y="830" class="display" font-size="190" fill="{FG}">Звонки.</text>
  <text x="80" y="1010" class="display-it" font-size="190" fill="{OLIVE}">Файлы.</text>
  <text x="80" y="1280" class="body" font-size="28" fill="{FG}">Корпоративный мессенджер · WebRTC · 90-day plan</text>
''')

# ============ 7. PROGRUZ — logistics, steel + warning orange ============
progruz = cover("progruz", f'''
  <rect width="{W}" height="{H}" fill="{BG}"/>
  <!-- Container/grid -->
  <g opacity="0.2" stroke="{FG}" stroke-width="1" fill="none">
    {''.join(f'<rect x="{60+col*180}" y="{220+row*180}" width="170" height="170" stroke-dasharray="4 6"/>' for col in range(7) for row in range(6))}
  </g>
  <!-- Highlighted "loaded" cells -->
  <g fill="{ACCENT2}" opacity="0.85">
    <rect x="60" y="220" width="170" height="170" rx="6"/>
    <rect x="240" y="400" width="170" height="170" rx="6"/>
    <rect x="420" y="220" width="170" height="170" rx="6"/>
  </g>
  <g fill="{OLIVE}" opacity="0.85">
    <rect x="600" y="400" width="170" height="170" rx="6"/>
    <rect x="780" y="220" width="170" height="170" rx="6"/>
  </g>
  <text x="80" y="120" class="mono" font-size="22" fill="{MUTED}">PROGRUZ · LOGISTICS SAAS</text>
  <line x1="80" y1="150" x2="180" y2="150" stroke="{ACCENT2}" stroke-width="1.5"/>
  <text x="80" y="980" class="display" font-size="180" fill="{FG}">Прогруз.</text>
  <text x="80" y="1140" class="display-it" font-size="100" fill="{ACCENT2}">Заказы. Рейсы. Машины.</text>
  <text x="80" y="1280" class="body" font-size="26" fill="{FG}">Диспетчеризация перевозок · Маршруты · Тарифы · Накладные</text>
  <text x="80" y="1330" class="mono" font-size="18" fill="{MUTED}">Next.js 15 · Matter.js physics · Framer Motion</text>
''')

covers = {
    "kanavto": kanavto,
    "klining-24": klining,
    "kuzovnsk": kuzovnsk,
    "fitness-saas": fitness,
    "lead-hunter": lead_hunter,
    "innertalk": innertalk,
    "progruz": progruz,
}

for slug, svg in covers.items():
    out_path = Path("/c/Users/12C2~1/AppData/Local/Temp/covers") / f"{slug}.svg"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")
    print(f"  {slug}.svg  ({len(svg):,} bytes)")

print(f"\nGenerated {len(covers)} SVG covers in {out_path.parent}")
