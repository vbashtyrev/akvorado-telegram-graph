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

Источники настроек: **`.env`** (или `environment` в docker compose) и **`config.yaml`** (секции `telegram`, `clickhouse`). Значения из окружения переопределяют значения из файла.

---

### Переменные окружения (`.env`)

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| **TELEGRAM_BOT_TOKEN** | да | Токен бота от [@BotFather](https://t.me/BotFather). |
| **TELEGRAM_ALLOWED_CHAT_IDS** | нет | ID чатов или групп (у группы — отрицательное число), через запятую. Пусто — бот отвечает в любом чате. Для продакшена лучше задать список, чтобы бот не был доступен всем. |
| **CLICKHOUSE_URL** | нет | URL ClickHouse. По умолчанию `http://127.0.0.1:8123` (при `network_mode: host` бот обращается к ClickHouse на том же хосте). |

---

### Файл `config.yaml`

Полный пример — **`config.example.yaml`**.

| Опция | Описание |
|-------|----------|
| **display_timezone** | Часовой пояс по умолчанию для графиков. Пользовательский пояс: меню → «Настройки» → смещение от UTC (например `+3`, `-5`). |
| **telegram.user_timezones_file** | Файл, где хранятся пояса пользователей. В Docker для сохранения между пересборками укажите путь в томе, например `/data/user_timezones.json`, и смонтируйте `./data:/data`. |
| **Таблицы и интервалы** (6 ч / 24 ч / 7 д) | Своя таблица и интервал агрегации для каждого периода. Чтобы цифры совпадали с веб-консолью Akvorado — см. следующий подраздел. |
| **bytes_expression** | Подставляется в SQL как фрагмент выражения. Только доверенное значение (например `Bytes * coalesce(SamplingRate, 1)`), без `;` и подзапросов — при компрометации конфига снижает риски. |

---

### Совпадение Min/Max с консолью Akvorado

В веб-интерфейсе Akvorado для длинных периодов используются агрегированные таблицы (например `flows_1h0m0s` с интервалом 3600 с). Чтобы цифры в боте совпадали с UI, в секции `clickhouse` в `config.yaml` укажите те же таблицы и интервалы:

- **24 ч и 7 д:** обычно `table_24h: "default.flows_1h0m0s"`, `interval_sec_24h: 3600`
- **6 ч:** обычно `table_6h: "default.flows_1m0s"`, `interval_sec_6h: 60`

Список таблиц в БД:
```sql
SELECT name FROM system.tables WHERE name LIKE 'flows%' AND name NOT LIKE '%_local';
```

---

### Применение изменений

После изменения `.env` или `config.yaml` перезапустите бота: `docker compose restart bot`.

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
