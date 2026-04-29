"""Tool-функции, доступные агентам через ReAct loop.

Каждый файл — один tool:
- fetch_site.py — глубокий парсер сайта
- dns_check.py — SPF/DKIM/MX
- whois_lookup.py — возраст домена
- record_weakness.py — записать LeadWeakness
- draft_message.py — положить сообщение в outbox (НЕ отправляет)
- read_thread.py — прочитать историю conversation
- update_company.py — обновить Company
- update_conversation_state.py — изменить Conversation.state
- escalate.py — эскалация человеку
- finish.py — управляющий tool окончания цикла

Каждый tool — это (1) JSON-схема для Anthropic tool-use, (2) Python-функция.
"""
