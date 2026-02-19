# Графики Akvorado (ClickHouse) в Telegram

Бот: по команде `/graph` или «График» показывает кнопки периода (1 ч, 6 ч, 24 ч, 7 д), запрашивает данные из ClickHouse (Akvorado) и отправляет в чат график трафика (битрейт, InIfBoundary = external).

ClickHouse должен быть уже запущен (в другом проекте; порт 8123 на хосте). В этом репозитории — только контейнер с ботом.

---

## Запуск (Docker Compose)

Требования: Docker 27.x, docker compose v2.

```bash
cd akvorado-telegram-graph
cp .env.example .env
cp config.example.yaml config.yaml
nano .env   # TELEGRAM_BOT_TOKEN=..., при необходимости TELEGRAM_ALLOWED_CHAT_IDS=-1001234567890
docker compose up -d
```

Логи: `docker compose logs -f bot`.

По умолчанию бот подключается к ClickHouse по `http://host.docker.internal:8123`. Другой адрес — задать `CLICKHOUSE_URL` в `.env` или в `config.yaml` (секция `clickhouse.url`).

---

## Конфигурация

- **TELEGRAM_BOT_TOKEN** — токен от [@BotFather](https://t.me/BotFather) (обязательно).
- **TELEGRAM_ALLOWED_CHAT_IDS** — ID чатов/групп через запятую (группа — отрицательное число); пусто = бот отвечает в любом чате.
- **CLICKHOUSE_URL** — URL ClickHouse (по умолчанию в compose: `http://host.docker.internal:8123`).

То же можно задать в `config.yaml` (секции `telegram` и `clickhouse`). Переменные окружения переопределяют значения из файла.
