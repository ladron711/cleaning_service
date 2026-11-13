FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 django \
    && useradd --uid 10001 --gid django --create-home django
COPY --chown=django:django manage.py ./
COPY --chown=django:django config ./config
COPY --chown=django:django main ./main
COPY --chown=django:django deploy ./deploy
RUN mkdir -p /app/staticfiles /app/media \
    && chown django:django /app/staticfiles /app/media
USER django
ENTRYPOINT ["python", "/app/deploy/entrypoint.py"]
CMD ["gunicorn", "--config", "deploy/gunicorn.conf.py", "config.wsgi:application"]
