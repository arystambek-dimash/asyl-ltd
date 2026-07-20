FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# gunicorn пиним: без версии содержимое прод-образа зависело бы от даты сборки.
RUN pip install --no-cache-dir -r requirements.txt "gunicorn>=23,<24"

COPY . .

COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
# --max-requests: воркер перезапускается после N запросов (страховка от утечек
#   памяти и «медленной деградации» при длительной нагрузке), jitter размывает
#   одновременный рестарт всех воркеров.
# --limit-request-* ограничивают строку запроса, число и размер заголовков —
#   отсекают заголовочные флуд-атаки на уровне сервера приложений.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--timeout", "60", "--graceful-timeout", "30", \
     "--keep-alive", "5", "--max-requests", "1000", "--max-requests-jitter", "100", \
     "--limit-request-line", "8190", "--limit-request-fields", "100", \
     "--limit-request-field_size", "16380", \
     "--access-logfile", "-", "--error-logfile", "-"]
