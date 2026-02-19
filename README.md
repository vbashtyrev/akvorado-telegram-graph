# Графики Akvorado (ClickHouse) в Telegram

Бот: по команде `/graph` или «График» показывает кнопки периода (1 ч, 6 ч, 24 ч, 7 д), запрашивает данные из ClickHouse (Akvorado) и отправляет в чат график трафика (битрейт, InIfBoundary = external).

ClickHouse должен быть уже запущен (в другом проекте; порт 8123 на хосте). В этом репозитории — только контейнер с ботом.

---

## Запуск на сервере (с нуля)

На сервере должны быть установлены Docker и docker compose v2. ClickHouse уже должен быть доступен (например, в другом контейнере с пробросом порта 8123 на хост).

**1. Клонировать репозиторий и перейти в каталог:**

```bash
git clone https://github.com/vbashtyrev/akvorado-telegram-graph.git
cd akvorado-telegram-graph
```

**2. Создать конфиг из примеров:**

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

**3. Заполнить `.env`** (токен от [@BotFather](https://t.me/BotFather); при необходимости — ID группы, куда писать):

```bash
nano .env
```

Указать:
- `TELEGRAM_BOT_TOKEN=...` — обязательно.
- `TELEGRAM_ALLOWED_CHAT_IDS=-1001234567890` — опционально; ID группы (отрицательное число). Несколько через запятую. Пусто — бот отвечает в любом чате.
- `CLICKHOUSE_URL` — по умолчанию в compose уже задан `http://host.docker.internal:8123` (ClickHouse на этом же хосте, порт 8123). Если ClickHouse на другом адресе — задать здесь.

**4. Запустить бота:**

```bash
docker compose up -d
```

**5. Проверить логи:**

```bash
docker compose logs -f bot
```

В Telegram: команда `/graph` или текст «График» — появятся кнопки выбора периода, после выбора бот пришлёт график.

Перезапуск: `docker compose restart bot`. Остановка: `docker compose down`.

---

## Конфигурация

- **TELEGRAM_BOT_TOKEN** — токен от [@BotFather](https://t.me/BotFather) (обязательно).
- **TELEGRAM_ALLOWED_CHAT_IDS** — ID чатов/групп через запятую (группа — отрицательное число); пусто = бот отвечает в любом чате.
- **CLICKHOUSE_URL** — URL ClickHouse (по умолчанию в compose: `http://host.docker.internal:8123`).

То же можно задать в `config.yaml` (секции `telegram` и `clickhouse`). Переменные окружения переопределяют значения из файла.
