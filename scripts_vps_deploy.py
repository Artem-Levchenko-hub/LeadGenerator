"""VPS deploy через paramiko. Пароль читаем из env SSH_PASS.

Использование:
    SSH_PASS='...' .venv/Scripts/python.exe scripts_vps_deploy.py [stage]

Stages:
    discover  — показать что мы можем (sudo? cwd? git state?)
    pull      — git pull, без перезапусков
    update_env — обновить .env под OpenRouter (3 ключа)
    restart   — restart services
    full      — discover + pull + update_env + restart + verify
"""
from __future__ import annotations

import os
import sys

import paramiko


# Windows-консоль cp1251 ломается на эмодзи/стрелках; форсим UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


HOST = "170.168.72.200"
USER = "i48ptgvnis"
PROJECT_DIR = "/home/i48ptgvnis/stenvik-leads"


def connect() -> paramiko.SSHClient:
    pw = os.environ.get("SSH_PASS")
    if not pw:
        sys.exit("ERROR: SSH_PASS env var not set")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=pw, timeout=20, look_for_keys=False, allow_agent=False)
    return ssh


def run(ssh: paramiko.SSHClient, cmd: str, *, label: str | None = None, timeout: int = 90) -> tuple[int, str, str]:
    label = label or cmd
    print(f"\n=== {label} ===", flush=True)
    stdin_, stdout, stderr = ssh.exec_command(cmd, timeout=timeout, get_pty=False)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    print(f"[rc={rc}]")
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err.strip():
        print("STDERR:", err, end="" if err.endswith("\n") else "\n")
    return rc, out, err


def stage_discover(ssh):
    run(ssh, "whoami; hostname; uname -srm", label="identity")
    run(ssh, f"ls -la {PROJECT_DIR} | head -25", label="project layout")
    run(ssh, f"cd {PROJECT_DIR} && git log --oneline -5", label="current git head")
    run(ssh, f"cd {PROJECT_DIR} && git status --short", label="git working tree")
    run(ssh, "sudo -n -l 2>&1 | head -20", label="sudo without password (non-interactive)")
    run(ssh, "systemctl --user list-units 2>/dev/null | head; systemctl is-active stenvik-worker stenvik-leads", label="services state")


def stage_pull(ssh):
    run(ssh, f"cd {PROJECT_DIR} && git fetch origin && git log --oneline HEAD..origin/main | head -5", label="commits to pull")
    run(ssh, f"cd {PROJECT_DIR} && git pull --ff-only origin main", label="git pull")
    run(ssh, f"cd {PROJECT_DIR} && git log --oneline -3", label="head after pull")


def stage_update_env_ceo(ssh):
    """Добавляет CEO ключ + модель в .env (если их там ещё нет).

    Ключ читаем из env CEO_OPENROUTER_API_KEY (никогда не хардкодим в коде).
    """
    ceo_key = os.environ.get("CEO_OPENROUTER_API_KEY")
    if not ceo_key:
        sys.exit("ERROR: CEO_OPENROUTER_API_KEY env var not set")
    ceo_model = os.environ.get("CEO_MODEL", "anthropic/claude-opus-4.7")
    # Проверяем есть ли уже строка ceo_openrouter_api_key — если нет, дописываем.
    run(
        ssh,
        f"cd {PROJECT_DIR} && grep -qi '^ceo_openrouter_api_key=' .env "
        f"|| (echo '' >> .env && echo 'ceo_openrouter_api_key={ceo_key}' >> .env "
        f"&& echo 'ceo_model={ceo_model}' >> .env && echo '[appended]')",
        label="append CEO keys to .env if missing",
    )
    run(
        ssh,
        f"cd {PROJECT_DIR} && sed -i -E 's|^([Cc][Ee][Oo]_[Oo][Pp][Ee][Nn][Rr][Oo][Uu][Tt][Ee][Rr]_[Aa][Pp][Ii]_[Kk][Ee][Yy])=.*|\\1={ceo_key}|' .env",
        label="ensure CEO key value (idempotent)",
    )
    run(
        ssh,
        f"cd {PROJECT_DIR} && grep -iE '^(ceo_openrouter_api_key|ceo_model)=' .env "
        "| sed -E 's|(api_key=.{14}).+|\\1...REDACTED|i'",
        label="verify CEO env",
    )


def stage_run_ceo_audit(ssh):
    run(
        ssh,
        f"cd {PROJECT_DIR} && .venv/bin/python -m worker.agents.ceo audit 2>&1",
        label="CEO audit run",
        timeout=240,
    )


def stage_update_env(ssh):
    """Обновляет основной OpenRouter-ключ + base_url + max_iter на VPS.

    Ключ читаем из env OPENROUTER_API_KEY (никогда не хардкодим в коде).
    """
    new_key = os.environ.get("OPENROUTER_API_KEY")
    if not new_key:
        sys.exit("ERROR: OPENROUTER_API_KEY env var not set")
    # Бэкап .env перед правкой
    run(ssh, f"cd {PROJECT_DIR} && cp .env .env.bak.$(date +%s) && ls -la .env*", label="backup .env")
    new_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    new_max = os.environ.get("OUTREACH_MAX_ITERATIONS", "8")
    # sed с | как разделителем чтобы не путаться с / в URL
    # На VPS ключи в .env lowercase. pydantic-settings case-insensitive, так что
    # имя переменной не имеет значения — но sed чувствителен. Используем -i -E с
    # регекспом, который ловит обе регистра-формы.
    cmds = [
        f"cd {PROJECT_DIR} && sed -i -E 's|^([Oo][Pp][Ee][Nn][Rr][Oo][Uu][Tt][Ee][Rr]_[Aa][Pp][Ii]_[Kk][Ee][Yy])=.*|\\1={new_key}|' .env",
        f"cd {PROJECT_DIR} && sed -i -E 's|^([Oo][Pp][Ee][Nn][Rr][Oo][Uu][Tt][Ee][Rr]_[Bb][Aa][Ss][Ee]_[Uu][Rr][Ll])=.*|\\1={new_url}|' .env",
        f"cd {PROJECT_DIR} && sed -i -E 's|^([Oo][Uu][Tt][Rr][Ee][Aa][Cc][Hh]_[Mm][Aa][Xx]_[Ii][Tt][Ee][Rr][Aa][Tt][Ii][Oo][Nn][Ss])=.*|\\1={new_max}|' .env",
    ]
    for c in cmds:
        run(ssh, c, label=c.split("&&", 1)[1].strip())
    # verify — печатаем relevant lines с маскированным ключом
    run(
        ssh,
        f"cd {PROJECT_DIR} && grep -iE '^(openrouter_api_key|openrouter_base_url|outreach_max_iterations|llm_provider|model_default|model_premium|http_proxy_url)=' .env "
        "| sed -E 's|(api_key=.{14}).+|\\1...REDACTED|i' "
        "| sed -E 's|(proxy_url=.{14}).+|\\1...REDACTED|i'",
        label="verify .env",
    )


def stage_restart(ssh):
    # systemctl restart обычно требует sudo — пробуем с sudo -n (если nopasswd настроен)
    # ВАЖНО: имена unit'ов на VPS остались stenvik-worker / stenvik-leads (инфраструктура
    # стабильна, только бренд внутри кода → Omnia Develop). Если будем переименовывать
    # systemd-units — отдельный шаг с pm2 stop / systemctl daemon-reload.
    run(ssh, "sudo -n systemctl restart stenvik-worker stenvik-leads 2>&1", label="restart (sudo -n)")
    run(ssh, "systemctl is-active stenvik-worker stenvik-leads", label="active state")
    run(ssh, "sudo -n journalctl -u stenvik-worker --since '30 seconds ago' --no-pager 2>&1 | tail -20", label="worker recent log")
    run(ssh, "sudo -n journalctl -u stenvik-leads --since '30 seconds ago' --no-pager 2>&1 | tail -10", label="web recent log")


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "discover"
    ssh = connect()
    try:
        if stage == "discover":
            stage_discover(ssh)
        elif stage == "pull":
            stage_pull(ssh)
        elif stage == "update_env":
            stage_update_env(ssh)
        elif stage == "restart":
            stage_restart(ssh)
        elif stage == "full":
            stage_discover(ssh)
            stage_pull(ssh)
            stage_update_env(ssh)
            stage_restart(ssh)
        elif stage == "ceo_setup":
            stage_pull(ssh)
            stage_update_env_ceo(ssh)
            stage_restart(ssh)
        elif stage == "ceo_run":
            stage_run_ceo_audit(ssh)
        else:
            sys.exit(f"unknown stage {stage!r}")
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
