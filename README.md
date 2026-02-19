# Графики Akvorado (ClickHouse) в Telegram

Телеграм-бот: в меню периоды **1 ч**, **6 ч**, **24 ч**, **7 д** — нажал пункт, бот присылает график трафика (битрейт по интерфейсам, InIfBoundary = external) и таблицу Min/Max/Last/Avg. Данные из ClickHouse (Akvorado).

ClickHouse должен быть уже развёрнут отдельно (порт 8123 на хосте). В репозитории — только образ и конфиг бота.

---

## Запуск (с нуля)

Нужны: Docker, docker compose v2, работающий ClickHouse (например, в другом контейнере с портом 8123 на хосте).

**1. Каталог проекта**

С git:
```bash
git clone https://github.com/vbashtyrev/akvorado-telegram-graph.git
cd akvorado-telegram-graph
```

Без git (curl + tar):
```bash
curl -L -o main.tar.gz https://github.com/vbashtyrev/akvorado-telegram-graph/archive/refs/heads/main.tar.gz
tar xzf main.tar.gz
cd akvorado-telegram-graph-main
```

**2. Конфиг**

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

В `.env` задать:
- **TELEGRAM_BOT_TOKEN** — токен от [@BotFather](https://t.me/BotFather) (обязательно).
- **TELEGRAM_ALLOWED_CHAT_IDS** — ID чата/группы (группа — отрицательное число), через запятую несколько; пусто — бот отвечает в любом чате.
- **CLICKHOUSE_URL** — по умолчанию `http://127.0.0.1:8123`. Бот запущен с `network_mode: host`, поэтому видит тот же хост; если ClickHouse на другом адресе — указать здесь.

**3. Запуск**

```bash
docker compose up -d
```

Логи: `docker compose logs -f bot`. В Telegram: меню бота → «1 ч» / «6 ч» / «24 ч» / «7 д» → приходит график и таблица.

Остановка: `docker compose down`. Перезапуск: `docker compose restart bot`.

---

## Обновление

**Установка через git:**
```bash
cd akvorado-telegram-graph
git pull
docker compose up -d --build
```

**Установка через архив:** скачать новый архив, распаковать в новую папку, скопировать туда `.env` и `config.yaml`, из новой папки выполнить `docker compose up -d --build`.

---

## Конфигурация

Переменные окружения (в `.env` или в `environment` compose) можно дублировать в `config.yaml` (секции `telegram`, `clickhouse`). Окружение переопределяет файл.

- **TELEGRAM_BOT_TOKEN** — обязательно.
- **TELEGRAM_ALLOWED_CHAT_IDS** — список ID чатов/групп через запятую. **Пусто = бот отвечает в любом чате.** Для продакшена рекомендуется задать список разрешённых чатов, чтобы бот не был доступен всем, кто узнает его имя.
- **CLICKHOUSE_URL** — по умолчанию `http://127.0.0.1:8123` (при `network_mode: host` бот обращается к ClickHouse на хосте).

В `config.yaml` также задаётся часовой пояс графиков по умолчанию (`display_timezone`), таблицы и интервалы для 6 ч / 24 ч (см. `config.example.yaml`). Каждый пользователь может задать свой пояс: кнопка **«Настройки»** в меню → ввод смещения от UTC в часах (например **+3** или **-5**). По умолчанию используется UTC. Сохранённые пояса лежат в файле (по умолчанию рядом с `config.yaml`; для Docker с сохранением между пересборками укажите `telegram.user_timezones_file`, например `/data/user_timezones.json`, и смонтируйте том `./data:/data`). Опция **bytes_expression** (если задана) подставляется в SQL как фрагмент выражения; указывайте только доверенное значение (например `Bytes * coalesce(SamplingRate, 1)`), без лишних операторов (`;`, подзапросы и т.п.), чтобы избежать рисков при компрометации конфига.

**Совпадение Min/Max с консолью Akvorado**  
Консоль для длинных периодов использует агрегированные таблицы (например `flows_1h0m0s` с интервалом 3600 с). Чтобы цифры в боте совпадали с UI, в секции `clickhouse` укажите те же таблицу и интервал: для 24 ч и 7 д — обычно `table_24h: "default.flows_1h0m0s"`, `interval_sec_24h: 3600`; для 6 ч — `table_6h: "default.flows_1m0s"`, `interval_sec_6h: 60`. Список таблиц в БД: `SELECT name FROM system.tables WHERE name LIKE 'flows%' AND name NOT LIKE '%_local';`

После смены `.env` или `config.yaml` нужен перезапуск: `docker compose restart bot`.

---

## Проверка ClickHouse

Бот с `network_mode: host` стучится на `127.0.0.1:8123`. Проверка с хоста:

```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8123/ping
```

Ожидается `200`. При ошибках соединения — убедиться, что ClickHouse слушает 8123 и доступен на хосте.

---

## Окружение и безопасность

- **network_mode: host** — бот запускается с сетевым режимом хоста, чтобы достучаться до ClickHouse на `127.0.0.1:8123`. Контейнер при этом использует сеть хоста как свою; при необходимости изоляции рассмотрите туннель (например `ssh -L`) или проброс порта без `host`.
- **Пользователь в контейнере** — образ по умолчанию запускает процесс от root. При повышенных требованиях к изоляции можно собрать свой образ с отдельным пользователем (`USER` в Dockerfile) или запускать бота вне Docker.
