FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt requirements-redis.txt ./
RUN pip install --no-cache-dir -r requirements-redis.txt \
    && groupadd --system katana \
    && useradd --system --gid katana katana \
    && install -d -o katana -g katana /data

COPY *.py alembic.ini ./
COPY migrations/ migrations/
USER katana
CMD ["python", "main.py"]
