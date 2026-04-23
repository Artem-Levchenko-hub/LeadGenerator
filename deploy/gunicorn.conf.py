"""Gunicorn config для Stenvik Leads.

Запуск:
    gunicorn app.main:app -c deploy/gunicorn.conf.py
"""
import multiprocessing
import os

bind = os.environ.get("STENVIK_BIND", "127.0.0.1:8001")
workers = int(os.environ.get("STENVIK_WORKERS", 2))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 60
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100

accesslog = "-"  # stdout → journal
errorlog = "-"
loglevel = os.environ.get("STENVIK_LOG_LEVEL", "info")

preload_app = True

# URL-based import через base64 — длинные URL до 16 КБ
limit_request_line = 16384
limit_request_fields = 200
limit_request_field_size = 16384
