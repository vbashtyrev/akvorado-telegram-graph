# Графики Akvorado (ClickHouse) в Telegram

Бот: в меню — периоды (1 ч, 6 ч, 24 ч, 7 д); нажал пункт — сразу график трафика из ClickHouse (Akvorado) в чат (битрейт, InIfBoundary = external).

ClickHouse должен быть уже запущен (в другом проекте; порт 8123 на хосте). В этом репозитории — только контейнер с ботом.

---

## Запуск на сервере (с нуля)

На сервере должны быть установлены Docker и docker compose v2. ClickHouse уже должен быть доступен (например, в другом контейнере с пробросом порта 8123 на хост).

**1. Скачать репозиторий и перейти в каталог**

Если установлен **git**:

```bash
git clone https://github.com/vbashtyrev/akvorado-telegram-graph.git
cd akvorado-telegram-graph
```

Если **git нет** — скачать архив через **curl** (есть по умолчанию в AlmaLinux и других) и распаковать **tar**:

```bash
curl -L -o main.tar.gz https://github.com/vbashtyrev/akvorado-telegram-graph/archive/refs/heads/main.tar.gz
tar xzf main.tar.gz
cd akvorado-telegram-graph-main
```

То же через wget (если установлен): `wget -O main.tar.gz https://github.com/vbashtyrev/akvorado-telegram-graph/archive/refs/heads/main.tar.gz`

Дальнейшие шаги те же: создать `.env` и `config.yaml`, заполнить, запустить `docker compose up -d`.

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

В Telegram: открой меню бота (кнопка слева от поля ввода) — пункты «1 ч», «6 ч», «24 ч», «7 д»; нажал — бот пришлёт график.

Перезапуск: `docker compose restart bot`. Остановка: `docker compose down`.

---

## Обновление кода на сервере

**Если ставили через git:** зайти в каталог, подтянуть изменения, пересобрать контейнер:

```bash
cd akvorado-telegram-graph
git pull
docker compose up -d --build
```

**Если ставили через архив (без git):** скачать новый архив, распаковать в новую папку, скопировать туда свой `.env` и `config.yaml`, затем из новой папки запустить `docker compose up -d --build`. Старый каталог можно удалить.

`--build` пересоберёт образ с новым кодом. Логи: `docker compose logs -f bot`.

---

## Конфигурация

- **TELEGRAM_BOT_TOKEN** — токен от [@BotFather](https://t.me/BotFather) (обязательно).
- **TELEGRAM_ALLOWED_CHAT_IDS** — ID чатов/групп через запятую (группа — отрицательное число); пусто = бот отвечает в любом чате.
- **CLICKHOUSE_URL** — URL ClickHouse (по умолчанию `http://127.0.0.1:8123`). Бот в Docker запущен с `network_mode: host`, поэтому видит тот же 127.0.0.1, что и хост (подходит, если ClickHouse проброшен как 127.0.0.1:8123).

То же можно задать в `config.yaml` (секции `telegram` и `clickhouse`). Переменные окружения переопределяют значения из файла.

**Цифры расходятся с консолью Akvorado?** Консоль для разных периодов использует разные таблицы: для 6 ч часто `flows_1m0s`, для 24/7 д — `flows_5m0s`. В `config.yaml` (секция `clickhouse`) задайте `table_6h: "default.flows_1m0s"`, `interval_sec_6h: 60` и при необходимости `table_24h` / `interval_sec_24h`, затем перезапустите бота.

**Изменили `.env` или `config.yaml`?** Бот подхватывает конфиг только при старте. Перезапустите контейнер:
```bash
docker compose restart bot
```

---

## Проверка доступности ClickHouse

Бот запущен с **network_mode: host**, поэтому использует сеть хоста и обращается к ClickHouse по `127.0.0.1:8123` так же, как с самого хоста. Достаточно, чтобы на хосте отвечал порт 8123:

```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8123/ping
```
Ожидается `200`. Если бот пишет Connection refused — проверьте, что контейнер ClickHouse запущен и порт проброшен как `127.0.0.1:8123->8123`.
