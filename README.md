# cleaning_service

Основа проекта: Django в `config`, приложение `main`, административная
панель `/admin/`. База данных — локальный PostgreSQL 16 (порт 5433).
Подключение задаётся через `.env`; автоматического переключения на SQLite нет.

## Локальный запуск

Из корня проекта в Bash:

```bash
source .venv/bin/activate
# Если окружения нет: python3 -m venv .venv, затем активировать его.
python -m pip install -r requirements.txt
# Если .env отсутствует, создать его со случайным ключом:
python - <<'PY'
from pathlib import Path
import secrets

path = Path('.env')
if not path.exists():
    content = Path('.env.example').read_text()
    content = content.replace('DJANGO_SECRET_KEY=', 'DJANGO_SECRET_KEY=' + secrets.token_urlsafe(50), 1)
    with path.open('x') as env_file:
        path.chmod(0o600)
        env_file.write(content)
PY
# До запуска заполните POSTGRES_* в .env и создайте БД по инструкции ниже.
python manage.py check
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Открыть http://127.0.0.1:8000/admin/ и войти созданным пользователем.
`migrate` применяет миграции проекта в локальной БД.

Настройки автоматически загружают `.env` из корня проекта через python-dotenv.
Переменные окружения имеют приоритет над `.env`. Локальный файл исключён из
Git; `.env.example` содержит шаблон без ключа.

`DJANGO_SECRET_KEY` обязателен; команда выше создаёт локальный файл только
при его отсутствии и не выводит ключ. `DJANGO_DEBUG` по умолчанию выключен,
в локальном `.env` включён;
`DJANGO_ALLOWED_HOSTS` — список через запятую, по умолчанию
`localhost,127.0.0.1,[::1]`, в локальном `.env` — `localhost,127.0.0.1`.
Загруженные файлы будут храниться в `media/`, исключённом из Git.

## PostgreSQL 16

Запускайте только кластер 16: `sudo pg_ctlcluster 16 main start`.
Кластер 14 не требуется. Проверьте порт командой `pg_lsclusters`.

Для первичной настройки создайте отдельную роль и чистую БД:

```bash
sudo -u postgres createuser -p 5433 --login --createdb --pwprompt cleaning_service
sudo -u postgres createdb -p 5433 --owner=cleaning_service --encoding=UTF8 --template=template0 cleaning_service
```

Укажите тот же пароль в локальном `.env` (`POSTGRES_PASSWORD`), а также
`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_HOST` и `POSTGRES_PORT` из
`.env.example`. Пароли не добавляются в Git. Право CREATEDB нужно локальной
роли для создания тестовой БД Django; права суперпользователя не нужны.
Драйвер `psycopg2-binary` уже включён в зависимости.

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py check
python manage.py test
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Тесты `main.test_postgresql`
проверяют ограничения, откат транзакции и блокировки между двумя соединениями.

## Docker Compose

Три сервиса: PostgreSQL 16, Django/Gunicorn и Nginx. Certbot работает внутри
Nginx. Требуются Docker Engine и Compose v2+. Локальная БД на хосте не затрагивается.

### Локальный запуск

В `.env` задайте отдельные случайные `POSTGRES_PASSWORD` и
`POSTGRES_ADMIN_PASSWORD`. `POSTGRES_USER` должен отличаться от `postgres`.
Существующие `POSTGRES_HOST`/`POSTGRES_PORT` оставьте для запуска без Docker:
Compose подставляет внутренние `db:5432`. `.env` не входит в образы.
Не публикуйте вывод `docker compose config` с раскрытыми секретами.

```bash
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 180
docker compose exec web python manage.py createsuperuser
```

Сайт: https://localhost:8443/, admin: https://localhost:8443/admin/.
HTTP на 8080 перенаправляется на HTTPS. `TLS_MODE=local` использует
самоподписанный сертификат: браузер предупредит, для curl допустим `-k`
только при локальной проверке. Домен не нужен.

Django работает без root, с `DEBUG=False`; Gunicorn и PostgreSQL не публикуют
порты. Роль приложения не имеет SUPERUSER/CREATEDB/CREATEROLE. Entry point
ждёт БД до 60 секунд, затем перед Gunicorn выполняет миграции и collectstatic.
Ошибка останавливает запуск. Nginx ждёт успешного healthcheck Django.
Именованные тома сохраняют БД, static, media и сертификаты. Nginx читает
static/media только для чтения. Галереи нет, фотографии доступны по прямым
ссылкам `/media/`, включая ссылки в admin.

### Проверки и обновление

```bash
docker compose exec web python manage.py check
docker compose exec web python manage.py check --deploy
sh deploy/test.sh
docker compose exec web python manage.py migrate --check
docker compose exec nginx nginx -t
docker compose ps
```

Тестовый скрипт создаёт отдельную `test_<POSTGRES_DB>` через администратора
БД и запускает тесты от роли приложения с `--keepdb`, без расширения её прав.
HTTPS-перенаправление отключается только в тестовом процессе, поскольку
Django test client использует HTTP. Тестовая БД сохраняется для повторных запусков.

Обновление после резервного копирования:
`docker compose up -d --build --force-recreate --wait --wait-timeout 180`.
Предусмотрен короткий перерыв в работе. Не запускайте несколько web-реплик
с этим entrypoint. `docker compose down` сохраняет данные;
**`docker compose down -v` удаляет тома с данными.** Пароли и init-скрипт
применяются только к новому тому PostgreSQL. Смена пароля существующей роли
требует отдельного ALTER ROLE, одного изменения `.env` недостаточно.

### VPS и HTTPS

Направьте DNS на VPS, откройте TCP 80/443. Замените примеры в `.env`:

```dotenv
SITE_DOMAIN=example.com
TLS_MODE=letsencrypt
ACME_EMAIL=admin@example.com
HTTP_PORT=80
HTTPS_PORT=443
DJANGO_ALLOWED_HOSTS=example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://example.com
DJANGO_HSTS_SECONDS=3600
```

Используйте отдельный production `DJANGO_SECRET_KEY`. Certbot получает
сертификат через HTTP-01 и проверяет необходимость обновления каждые 12 часов;
Nginx перечитывает сертификат без остановки. После ошибки повтор через час.
До первого успешного выпуска используется временный самоподписанный
сертификат, healthcheck остаётся неуспешным. Проверьте состояние и сертификат
перед публикацией сайта. Проверка обновления на VPS:
`docker compose exec nginx certbot renew --dry-run`.

`check --deploy` по умолчанию предупреждает о выключенных HSTS includeSubDomains
и preload (W005/W021). Это не ошибки запуска. Включайте
`DJANGO_HSTS_INCLUDE_SUBDOMAINS=True` только если все поддомены работают через
HTTPS, а `DJANGO_HSTS_PRELOAD=True` — при осознанном решении о preload;
для подачи в preload также требуется HSTS не менее года. Локально эти опции
не нужны. HTTPS и secure cookies включены независимо от них.

Настройте действующий SMTP (`SMTP_*`, `DEFAULT_FROM_EMAIL`), если нужна почта.
`check --deploy` не проверяет доставку. Логи идут в stdout/stderr, Docker
ограничивает размер и количество файлов; access log исключает query string
и тело POST. Не передавайте персональные данные в URL-пути.

### Резервное копирование

Настройте расписание, хранение копий вне VPS и проверку восстановления
в отдельную БД. Пример создания копий без вывода их содержимого:

```bash
umask 077
docker compose exec -T db sh -c 'pg_dump -U postgres -Fc "$APP_DB_NAME"' > database.dump
docker compose exec -T web tar -C /app/media -czf - . > media.tar.gz
```

Защищённо сохраняйте `.env` и том сертификатов. Дампы/архивы не добавляются
в Git или Docker build context.

### Без Docker и будущий бот

Первоначальный запуск через `.venv` и локальный PostgreSQL сохранён.
Для HTTP не включайте `DJANGO_HTTPS`, а при прямом доступе к Django —
`DJANGO_TRUST_PROXY`. Compose задаёт эти параметры только своим контейнерам.

Будущий бот сможет использовать тот же образ с командой
`python manage.py <bot_command>`, параметрами БД, сетью `database` и
сетью `frontend` для исходящих запросов к Telegram (без публикации портов),
а также зависимостью от готовности web. Entry point ждёт БД, но не выполняет миграции
для команд бота. Используйте Django ORM и `main.services` для изменений.
Сервис бота сейчас не создаётся.
