# cleaning_service

Основа проекта: Django в `config`, приложение `main`, административная
панель `/admin/`. Публичные страницы пока не реализованы.
Для текущего локального этапа используется SQLite; PostgreSQL из ТЗ
будет подключён отдельно.

## Локальный запуск

Из корня проекта в Bash:

```bash
source .venv/bin/activate
# Если окружения нет: python3 -m venv .venv, затем активировать его.
python -m pip install -r requirements.txt
export DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export DJANGO_DEBUG=True
python manage.py check
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Открыть http://127.0.0.1:8000/admin/ и войти созданным пользователем.
`migrate` создаёт таблицы стандартных приложений Django в локальной БД.

`DJANGO_SECRET_KEY` обязателен; команда выше генерирует временный локальный
ключ без вывода его значения. Для постоянного запуска храните стабильный
ключ в окружении. `DJANGO_DEBUG` по умолчанию выключен;
`DJANGO_ALLOWED_HOSTS` — список через запятую, по умолчанию
`localhost,127.0.0.1,[::1]`. Файлы `.env` автоматически не загружаются.
Загруженные файлы будут храниться в `media/`, исключённом из Git.
