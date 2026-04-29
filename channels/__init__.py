"""Каналы исходящей связи. Pluggable архитектура: новый канал = новый модуль
с функциями `send_sync(outbox_message) -> dict` и (опц.) `poll_inbound() -> list`.
"""
