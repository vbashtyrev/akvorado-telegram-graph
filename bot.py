#!/usr/bin/env python3
"""
Telegram-бот: график трафика Akvorado (ClickHouse) с выбором периода.
Меню бота или /graph, или «График» → кнопки 1 ч / 6 ч / 24 ч / 7 д → запрос в CH → картинка в чат.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import datetime, timezone, timedelta

import requests
import yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# python-telegram-bot 21.x
from telegram import BotCommand, MenuButtonCommands, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Периоды: (callback_suffix, label, hours, interval_sec для агрегации)
PERIODS = [
    ("1h", "1 ч", 1, 60),
    ("6h", "6 ч", 6, 60),
    ("24h", "24 ч", 24, 300),
    ("7d", "7 д", 7 * 24, 300),
]


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def apply_env_overrides(config: dict) -> None:
    """Переопределить конфиг из переменных окружения (Docker/Compose)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        config.setdefault("telegram", {})["bot_token"] = token
    chat_ids = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS")
    if chat_ids is not None:
        try:
            ids = [int(x.strip()) for x in chat_ids.split(",") if x.strip()]
            config.setdefault("telegram", {})["allowed_chat_ids"] = ids
        except ValueError:
            pass
    ch_url = os.environ.get("CLICKHOUSE_URL")
    if ch_url:
        config.setdefault("clickhouse", {})["url"] = ch_url.rstrip("/")


def fetch_bps_series(url: str, table: str, boundary: str, time_from: datetime, time_to: datetime, interval_sec: int) -> tuple[list[datetime], list[float], str | None]:
    """
    Запрос в ClickHouse: временной ряд bps (бит/с).
    Возвращает (timestamps, bps_list, error_message или None).
    """
    if "." in table and not table.startswith("."):
        db, tbl = table.split(".", 1)
        tbl_sql = "`{}`.`{}`".format(db.replace("`", "``"), tbl.replace("`", "``"))
    else:
        tbl_sql = "`{}`".format(table.replace("`", "``"))
    bytes_expr = "Bytes * coalesce(SamplingRate, 1)"
    where_extra = "WHERE InIfBoundary = '{}' ".format(str(boundary).replace("'", "''"))
    tz_suffix = ", 'UTC'"
    from_ts = time_from.strftime("%Y-%m-%d %H:%M:%S")
    to_ts = time_to.strftime("%Y-%m-%d %H:%M:%S")

    if interval_sec == 60:
        sql = (
            "SELECT toStartOfMinute(TimeReceived) AS minute, sum({bytes}) * 8 / 60 AS bps "
            "FROM {tbl} "
            "{where_extra}"
            "AND TimeReceived >= toDateTime('{from_ts}'{tz}) AND TimeReceived < toDateTime('{to_ts}'{tz}) "
            "GROUP BY minute ORDER BY minute FORMAT TabSeparated"
        ).format(bytes=bytes_expr, tbl=tbl_sql, where_extra=where_extra, from_ts=from_ts, to_ts=to_ts, tz=tz_suffix)
    else:
        sql = (
            "SELECT toStartOfInterval(TimeReceived, INTERVAL {interval} SECOND) AS minute, sum({bytes}) * 8 / {interval} AS bps "
            "FROM {tbl} "
            "{where_extra}"
            "AND TimeReceived >= toDateTime('{from_ts}'{tz}) AND TimeReceived < toDateTime('{to_ts}'{tz}) "
            "GROUP BY minute ORDER BY minute FORMAT TabSeparated"
        ).format(bytes=bytes_expr, tbl=tbl_sql, interval=interval_sec, where_extra=where_extra, from_ts=from_ts, to_ts=to_ts, tz=tz_suffix)

    try:
        r = requests.post(
            url.rstrip("/") + "/",
            data=sql.encode("utf-8"),
            timeout=120,
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        r.raise_for_status()
    except requests.RequestException as e:
        return [], [], "ClickHouse: {}".format(e)

    timestamps = []
    values = []
    for line in r.text.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            try:
                ts_str = parts[0].strip()[:19]
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                timestamps.append(dt)
                values.append(float(parts[1]))
            except (ValueError, TypeError):
                continue
    if not timestamps:
        return [], [], "За выбранный период данных нет (или таблица/фильтр не совпадают)."
    return timestamps, values, None


def build_graph_png(timestamps: list[datetime], bps_list: list[float], period_label: str) -> io.BytesIO:
    """Строит график битрейта (Mbps), возвращает PNG в BytesIO."""
    fig, ax = plt.subplots(figsize=(10, 5))
    mbps = [b / 1e6 for b in bps_list]
    ax.fill_between(timestamps, 0, mbps, alpha=0.4)
    ax.plot(timestamps, mbps, color="C0", linewidth=1)
    ax.set_ylabel("Мбит/с")
    ax.set_xlabel("Время (UTC)")
    ax.set_title("Трафик (InIfBoundary = external), период: {}".format(period_label))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M", tz=timezone.utc))
    plt.xticks(rotation=25)
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close()
    buf.seek(0)
    return buf


async def cmd_graph(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = context.bot_data.get("config") or {}
    allowed = config.get("telegram", {}).get("allowed_chat_ids") or []
    if allowed and update.effective_chat and update.effective_chat.id not in allowed:
        await update.message.reply_text("Команда недоступна в этом чате.")
        return
    keyboard = [
        [InlineKeyboardButton(label, callback_data="period_" + key) for key, label, _, _ in PERIODS],
    ]
    await update.message.reply_text(
        "Выберите период для графика трафика (Akvorado / ClickHouse):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def on_text_graph(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = (update.message.text or "").strip().lower()
    if text != "график":
        return
    config = context.bot_data.get("config") or {}
    allowed = config.get("telegram", {}).get("allowed_chat_ids") or []
    if allowed and update.effective_chat and update.effective_chat.id not in allowed:
        return
    keyboard = [
        [InlineKeyboardButton(label, callback_data="period_" + key) for key, label, _, _ in PERIODS],
    ]
    await update.message.reply_text(
        "Выберите период:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def on_period_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not query.data or not query.data.startswith("period_"):
        return
    key = query.data.replace("period_", "", 1)
    config = context.bot_data.get("config") or {}
    allowed = config.get("telegram", {}).get("allowed_chat_ids") or []
    if allowed and update.effective_chat and update.effective_chat.id not in allowed:
        await query.edit_message_text("Недоступно в этом чате.")
        return

    entry = next((p for p in PERIODS if p[0] == key), None)
    if not entry:
        await query.edit_message_text("Неизвестный период.")
        return
    _, label, hours, interval_sec = entry

    ch_cfg = config.get("clickhouse", {})
    url = ch_cfg.get("url", "http://localhost:8123")
    table = ch_cfg.get("table", "default.flows")
    boundary = ch_cfg.get("boundary_filter", "external")

    time_to = datetime.now(timezone.utc)
    time_from = time_to - timedelta(hours=hours)

    await query.edit_message_text("Запрашиваю данные за {}…".format(label))

    timestamps, bps_list, err = fetch_bps_series(url, table, boundary, time_from, time_to, interval_sec)
    if err:
        await query.edit_message_text("Ошибка: {}".format(err))
        return

    try:
        buf = build_graph_png(timestamps, bps_list, label)
    except Exception as e:
        await query.edit_message_text("Ошибка построения графика: {}".format(e))
        return

    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=buf,
        caption="Период: {} (UTC).".format(label),
    )
    await query.edit_message_text("График отправлен.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram-бот: график трафика Akvorado по выбору периода")
    parser.add_argument("-c", "--config", default="config.yaml", help="Путь к config.yaml")
    args = parser.parse_args()

    config = {}
    if os.path.isfile(args.config):
        try:
            loaded = load_config(args.config)
            config = loaded if isinstance(loaded, dict) else {}
        except Exception as e:
            print("Ошибка загрузки конфига: {}".format(e), file=sys.stderr)
            return 1
    elif not os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("Конфиг не найден: {} и TELEGRAM_BOT_TOKEN не задан. Скопируйте config.example.yaml в config.yaml или задайте переменные окружения.".format(args.config), file=sys.stderr)
        return 1

    # Минимальный конфиг по умолчанию (Docker/Compose)
    config.setdefault("telegram", {})
    config.setdefault("clickhouse", {"url": "http://clickhouse:8123", "table": "default.flows", "boundary_filter": "external"})
    apply_env_overrides(config)

    token = (config.get("telegram") or {}).get("bot_token")
    if not token:
        print("В config.yaml задайте telegram.bot_token.", file=sys.stderr)
        return 1

    async def post_init(application: Application) -> None:
        """Меню бота: команды и кнопка меню (вместо ввода /graph)."""
        await application.bot.set_my_commands([
            BotCommand("graph", "График трафика за период"),
        ])
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )
    app.bot_data["config"] = config

    app.add_handler(CommandHandler("graph", cmd_graph))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_graph))
    app.add_handler(CallbackQueryHandler(on_period_callback))

    print("Бот запущен. Меню (кнопка слева от поля ввода) или /graph, или «График» — выбор периода.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
