# Akvorado Telegram Graph Bot — графики трафика из ClickHouse в Telegram
# Docker 27.x, для использования с docker-compose

FROM python:3.11-slim-bookworm

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends fontconfig; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Конфиг монтируется в /config/config.yaml или задаётся через env
ENV MPLBACKEND=Agg

CMD ["python", "-u", "bot.py", "-c", "/config/config.yaml"]
