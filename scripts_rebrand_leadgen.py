"""Smart rebrand Omnia → Omnia Develop в lead-generator репо.

Правила:
- "Omnia Develop" → "Omnia Develop"
- "OMNIA DEVELOP" → "OMNIA DEVELOP"
- "OMNIADEVELOP.COM" → "OMNIADEVELOP.COM"
- "hello@omniadevelop.com" → "hello@omniadevelop.com"
- "omniadevelop.com" → "omniadevelop.com"
- \bSTENVIK\b (не followed by underscore — НЕ STENVIK_CONTEXT) → "OMNIA"
- \bStenvik\b → "Omnia"
- \bstenvik\b (не followed by "-leads" — НЕ stenvik-leads path) → "omnia"

ВАЖНО НЕ ТРОГАТЬ:
- STENVIK_CONTEXT, STENVIK_USP и пр. константы (имена identifiers)
- stenvik-leads (имя папки на VPS, путь в systemd unit'ах)
- C:\\Omnia (имя локальной папки)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(r"C:\Omnia")
EXCLUDE_DIR_NAMES = {".venv", "node_modules", "data", ".next", ".git",
                     ".pytest_cache", "__pycache__"}
INCLUDE_EXTS = {".py", ".md", ".txt", ".yml", ".yaml", ".sh", ".cfg", ".toml",
                ".json", ".html", ".css", ".tsx", ".ts"}
EXTRA_NAMES = {".env.example"}  # явно включаем

PATTERNS = [
    (re.compile(r"\bStenvik Studio\b"), "Omnia Develop"),
    (re.compile(r"\bSTENVIK STUDIO\b"), "OMNIA DEVELOP"),
    (re.compile(r"\bSTENVIK\.STUDIO\b"), "OMNIADEVELOP.COM"),
    (re.compile(r"hello@omnia\.studio"), "hello@omniadevelop.com"),
    (re.compile(r"\bstenvik\.studio\b"), "omniadevelop.com"),
    # \b (word boundary) ставим снаружи; STENVIK_ имеет _ (word char)
    # → boundary НЕ срабатывает, не трогаем константы.
    (re.compile(r"\bSTENVIK\b(?!_)"), "OMNIA"),
    (re.compile(r"\bStenvik\b"), "Omnia"),
    # stenvik-leads — folder path, не трогаем. Используем negative lookahead.
    (re.compile(r"\bstenvik\b(?!-leads)"), "omnia"),
    # smtp_from_name="Omnia" в .env уже покрыт паттерном 7
]

count = 0
detail = []

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    if any(p in EXCLUDE_DIR_NAMES for p in path.parts):
        continue
    if path.suffix.lower() not in INCLUDE_EXTS and path.name not in EXTRA_NAMES:
        continue

    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        continue

    new = text
    for pat, rep in PATTERNS:
        new = pat.sub(rep, new)

    if new != text:
        path.write_text(new, encoding="utf-8")
        rel = path.relative_to(ROOT)
        detail.append(str(rel))
        count += 1

print(f"Patched {count} files:")
for d in detail:
    print(f"  ✓ {d}")
print()

# Verify: any remaining mentions in source files?
remaining = []
for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    if any(p in EXCLUDE_DIR_NAMES for p in path.parts):
        continue
    if path.suffix.lower() not in INCLUDE_EXTS and path.name not in EXTRA_NAMES:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        continue
    for line_no, line in enumerate(text.splitlines(), 1):
        # Допустимые упоминания: stenvik-leads, STENVIK_*, локальный путь Windows
        if re.search(r"\bstenvik\b(?!-leads)", line, re.IGNORECASE):
            # пропускаем если это явно путь
            if "stenvik-leads" in line or r"C:\Omnia" in line or "C:\\Omnia" in line:
                continue
            if not (line.strip().startswith("#") and "stenvik-leads" in line):
                # Если это текстовое упоминание, не покрытое паттерном
                if "STENVIK_" in line:
                    continue  # constants
                if "stenvik-leads" in line:
                    continue
                remaining.append(f"{path.relative_to(ROOT)}:{line_no} {line.strip()[:120]}")

if remaining:
    print(f"⚠ {len(remaining)} possible leftover Omnia mentions (review):")
    for r in remaining[:20]:
        print(f"  {r}")
else:
    print("✓ No Omnia leftover (excluding path/constant references)")
