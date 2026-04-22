# Деплой на VPS (Ubuntu 22.04 / 24.04)

Подходит для Timeweb / Reg.ru / Selectel / Yandex Cloud. Требования: 1 CPU, 1 GB RAM, 10 GB диск. ~250-500₽/мес.

## 1. Первоначальная настройка VPS

```bash
# Подключиться по SSH как root
ssh root@<IP-адрес-сервера>

# Обновить систему
apt update && apt upgrade -y

# Установить нужные пакеты
apt install -y python3-venv python3-pip nginx git certbot python3-certbot-nginx ufw

# Создать системного пользователя
adduser --system --group --home /opt/lead_pipeline stenvik

# Настроить файрвол
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
```

## 2. Залить код

**Вариант А — через git (рекомендуется):**
```bash
cd /opt
git clone <репозиторий> lead_pipeline
chown -R stenvik:stenvik /opt/lead_pipeline
```

**Вариант Б — через scp с твоего компьютера:**
```bash
# На локальной машине
scp -r "D:/Новая папка/lead_pipeline/" root@<IP>:/opt/
# На сервере
chown -R stenvik:stenvik /opt/lead_pipeline
```

## 3. Создать venv и установить зависимости

```bash
cd /opt/lead_pipeline
sudo -u stenvik python3 -m venv .venv
sudo -u stenvik .venv/bin/pip install -r requirements.txt
```

## 4. Настроить .env

```bash
cp .env.example .env
nano .env
# Вписать:
#   ANTHROPIC_API_KEY=sk-ant-...
#   APP_SECRET=<длинная случайная строка, openssl rand -hex 32>
#   AUTH_USERS=admin:<надёжный пароль>,vlad:<пароль>,irina:<пароль>
```

Затем:
```bash
chown stenvik:stenvik .env
chmod 600 .env
mkdir -p /var/log/lead_pipeline
chown stenvik:stenvik /var/log/lead_pipeline
mkdir -p /opt/lead_pipeline/data
chown stenvik:stenvik /opt/lead_pipeline/data
```

## 5. Запустить как systemd-сервис

```bash
cp deploy/lead-pipeline.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lead-pipeline
systemctl status lead-pipeline
# Логи: journalctl -u lead-pipeline -f
```

## 6. Настроить Nginx + HTTPS

```bash
# Заменить домен в конфиге на свой
sed -i 's/leads.stenvik.studio/<ваш-домен>/g' deploy/nginx.conf
cp deploy/nginx.conf /etc/nginx/sites-available/lead-pipeline
ln -sf /etc/nginx/sites-available/lead-pipeline /etc/nginx/sites-enabled/

# Перед получением сертификата временно закомментировать блок SSL в конфиге
# (certbot сам его перепишет)
nginx -t && systemctl reload nginx

# Получить сертификат Let's Encrypt
certbot --nginx -d <ваш-домен> --agree-tos -m <ваш-email> --no-eff-email

# Автообновление сертификата включено по умолчанию (systemd таймер certbot.timer)
```

## 7. Проверить

```bash
curl -I https://<ваш-домен>/health
# → HTTP/2 401  (нужна auth — всё правильно, сервис работает)

# Открыть в браузере: https://<ваш-домен>/
# Войти под admin из AUTH_USERS
```

## 8. Первый запуск пайплайна

Сначала проверь вручную, что всё работает:
```bash
cd /opt/lead_pipeline
sudo -u stenvik .venv/bin/python run.py pipeline 3
```

Если отработало — планировщик в сервисе будет дёргать пайплайн каждые 15 минут автоматически.

## Обновления

```bash
cd /opt/lead_pipeline
sudo -u stenvik git pull
sudo -u stenvik .venv/bin/pip install -r requirements.txt
systemctl restart lead-pipeline
```

## Мониторинг

```bash
# Логи приложения
journalctl -u lead-pipeline -f --since "1 hour ago"

# Ошибки gunicorn
tail -f /var/log/lead_pipeline/error.log

# БД (размер, количество лидов)
sudo -u stenvik sqlite3 /opt/lead_pipeline/data/leads.db "select count(*) from leads;"

# Статус сервиса
systemctl status lead-pipeline
```

## Бэкап БД (раз в сутки, в cron)

```bash
# Добавить в crontab root-а
echo "0 3 * * * cp /opt/lead_pipeline/data/leads.db /var/backups/leads-$(date +\%Y\%m\%d).db" | crontab -
```
