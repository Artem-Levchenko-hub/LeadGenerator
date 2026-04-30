#!/bin/bash
# Rebrand: Omnia → Omnia Develop. Run from /home/i48ptgvnis/site.
set -e
cd /home/i48ptgvnis/site

echo "=== Backup ==="
TS=$(date +%s)
mkdir -p .rebrand-bak/$TS
cp -r src .rebrand-bak/$TS/src
cp storage/works.json .rebrand-bak/$TS/
echo "  → .rebrand-bak/$TS/"

echo ""
echo "=== Patching files ==="
TARGETS=(
  src/app/layout.tsx
  src/app/sitemap.ts
  src/app/robots.ts
  src/app/contact/page.tsx
  src/app/careers/page.tsx
  src/app/careers/[id]/page.tsx
  src/components/Header.tsx
  src/components/Footer.tsx
  src/components/Loader.tsx
  src/components/CommandPalette.tsx
  src/components/admin/InboxView.tsx
  src/lib/careers.ts
  src/lib/content.ts
  README.md
  storage/works.json
)

for f in "${TARGETS[@]}"; do
  if [ ! -f "$f" ]; then
    echo "  skip (missing): $f"
    continue
  fi
  # Order matters — longest specific patterns first to avoid double-rewrites.
  sed -i \
    -e 's|Omnia Develop|Omnia Develop|g' \
    -e 's|OMNIA DEVELOP|OMNIA DEVELOP|g' \
    -e 's|OMNIA\.STUDIO|OMNIADEVELOP.COM|g' \
    -e 's|OMNIA|OMNIA|g' \
    -e 's|hello@omnia\.studio|hello@omniadevelop.com|g' \
    -e 's|t\.me/stenvikstudio|t.me/omniadevelop|g' \
    -e 's|t\.me/omnia|t.me/omniadevelop|g' \
    -e 's|https://omnia\.studio|https://omniadevelop.com|g' \
    -e 's|omnia\.studio|omniadevelop.com|g' \
    -e 's|omnia-loaded|omnia-loaded|g' \
    -e 's|omnia studio|omnia develop|g' \
    -e 's|Omnia|Omnia|g' \
    -e 's|omnia|omnia|g' \
    "$f"
  echo "  ✓ $f"
done

echo ""
echo "=== Verify no Omnia leakage ==="
LEAK=$(grep -rEni "omnia|Omnia" src/ storage/works.json README.md 2>/dev/null | grep -v ".rebrand-bak" | grep -v ".bak" | head -10)
if [ -z "$LEAK" ]; then
  echo "  ✓ Clean — no Omnia strings remain"
else
  echo "$LEAK"
fi

echo ""
echo "=== Done ==="
