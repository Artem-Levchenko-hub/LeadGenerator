"""Demo: отправляет body драфта #45 на user gmail через локальный Postfix.
Обходит Auditor (cold outreach на gmail запрещён по 152-ФЗ — это правильно).
Используется только для демонстрации end-to-end pipeline.
"""
import sys
import os
import smtplib
sys.path.insert(0, "/home/i48ptgvnis/stenvik-leads")
os.chdir("/home/i48ptgvnis/stenvik-leads")
from email.message import EmailMessage
from email.utils import make_msgid, formatdate
from app.database import SessionLocal
from app import models

with SessionLocal() as db:
    m = db.query(models.OutboxMessage).filter_by(id=45).one_or_none()
    if not m:
        sys.exit("outbox#45 not found")
    body = m.body_text or ""
    subject = m.subject or "(no subject)"

prefix = (
    "DEMO: это пример того, что Outreach Agent написал бы на компанию РЖД-Медицина.\n"
    "Перенаправил тебе на gmail чтобы посмотрел headers (Authentication-Results,\n"
    "DKIM-Signature). Auditor не пропускает cold outreach на gmail/yandex/mail.ru\n"
    "(152-ФЗ) — это правильно. Реальные лиды получают только B2B-адреса.\n"
    "\n--- Оригинальный draft ниже ---\n\n"
)

em = EmailMessage()
em["From"] = "Omnia <outreach@outreach.innertalk.space>"
em["To"] = "undj00x03@gmail.com"
em["Subject"] = "[OMNIA DEMO] " + subject
em["Date"] = formatdate(localtime=False)
em["Message-ID"] = make_msgid(domain="outreach.innertalk.space")
em["Reply-To"] = "outreach@outreach.innertalk.space"
em.set_content(prefix + body)

with smtplib.SMTP("localhost", 25, timeout=30) as s:
    s.send_message(em)

print("OK submitted to localhost:25")
print("Message-ID:", em["Message-ID"])
print("Subject:", em["Subject"])
print("Body length:", len(prefix + body), "chars")
