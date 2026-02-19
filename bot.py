#!/usr/bin/env python3
"""
Telegram-бот: график трафика Akvorado (ClickHouse) с выбором периода.
В меню бота — периоды (1 ч, 6 ч, 24 ч, 7 д); нажал → сразу график в чат.
"""
from __future__ import annotations

import argparse
import io
import os
import re
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
    tz_env = os.environ.get("DISPLAY_TIMEZONE")
    if tz_env:
        config["display_timezone"] = tz_env.strip()


def is_chat_allowed(config: dict, chat_id: int | None) -> bool:
    """Разрешён ли чат: пустой allowed_chat_ids — любой; иначе только из списка."""
    allowed = config.get("telegram", {}).get("allowed_chat_ids") or []
    if not allowed:
        return True
    if chat_id is None:
        return False
    return chat_id in allowed


def _tbl_sql(table: str) -> str:
    if "." in table and not table.startswith("."):
        db, tbl = table.split(".", 1)
        return "`{}`.`{}`".format(db.replace("`", "``"), tbl.replace("`", "``"))
    return "`{}`".format(table.replace("`", "``"))


def fetch_bps_by_interface(
    url: str,
    table: str,
    boundary: str,
    time_from: datetime,
    time_to: datetime,
    interval_sec: int,
    period_hours: float = 1,
    bytes_expression: str | None = None,
) -> tuple[dict[tuple[str, str], list[tuple[datetime, float]]], list[dict], str | None]:
    """
    Запрос в ClickHouse: InIfBoundary = boundary, группировка по ExporterName, InIfName.
    bytes_expression: формула байт для bps. По умолчанию Bytes*SamplingRate — в консоли Akvorado
    это и есть L3 (console/clickhouse.go: l3bps = SUM(Bytes*SamplingRate*8)).
    """
    tbl_sql = _tbl_sql(table)
    bytes_expr = (bytes_expression or "Bytes * coalesce(SamplingRate, 1)").strip()
    where_extra = "WHERE InIfBoundary = '{}' ".format(str(boundary).replace("'", "''"))
    tz_suffix = ", 'UTC'"
    from_ts = time_from.strftime("%Y-%m-%d %H:%M:%S")
    to_ts = time_to.strftime("%Y-%m-%d %H:%M:%S")
    if interval_sec == 60:
        time_expr = "toStartOfMinute(TimeReceived)"
        rate_div = "60"
    else:
        time_expr = "toStartOfInterval(TimeReceived, INTERVAL {} SECOND)".format(interval_sec)
        rate_div = str(interval_sec)
    sql = (
        "SELECT ExporterName, InIfName, {time_expr} AS minute, sum({bytes}) * 8 / {rate} AS bps "
        "FROM {tbl} "
        "{where_extra}"
        "AND TimeReceived >= toDateTime('{from_ts}'{tz}) AND TimeReceived < toDateTime('{to_ts}'{tz}) "
        "GROUP BY ExporterName, InIfName, minute ORDER BY ExporterName, InIfName, minute FORMAT TabSeparated"
    ).format(
        time_expr=time_expr, bytes=bytes_expr, rate=rate_div, tbl=tbl_sql,
        where_extra=where_extra, from_ts=from_ts, to_ts=to_ts, tz=tz_suffix,
    )
    try:
        r = requests.post(
            url.rstrip("/") + "/",
            data=sql.encode("utf-8"),
            timeout=120,
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        r.raise_for_status()
    except requests.RequestException as e:
        return {}, [], "ClickHouse: {}".format(e)

    series: dict[tuple[str, str], list[tuple[datetime, float]]] = {}
    for line in r.text.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 4:
            try:
                exporter, inif = parts[0].strip(), parts[1].strip()
                ts_str = parts[2].strip()[:19]
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                bps = float(parts[3])
                key = (exporter, inif)
                series.setdefault(key, []).append((dt, bps))
            except (ValueError, TypeError):
                continue
    if not series:
        return {}, [], "За выбранный период данных нет (InIfBoundary = {}).".format(boundary)

    # Обрезка краёв: для 1 ч/6 ч — 1 интервал (неполные минуты по краям). Для 24 ч/7 д не обрезаем — чтобы Min совпадал с UI (770 Мбит и т.д.)
    trim_buckets = 0 if period_hours >= 24 else 1
    stats = []
    for (exporter, inif), points in series.items():
        if not points:
            continue
        bps_vals = [p[1] for p in points]
        n = len(bps_vals)
        if n > trim_buckets * 2:
            trimmed = bps_vals[trim_buckets:-trim_buckets]
        else:
            trimmed = bps_vals
        if not trimmed:
            trimmed = bps_vals
        n_t = len(trimmed)
        min_bps = min(trimmed)
        max_bps = max(trimmed)
        last_bps = trimmed[-1]
        avg_bps = sum(trimmed) / n_t
        sorted_bps = sorted(trimmed)
        p95_bps = sorted_bps[int((n_t - 1) * 0.95)] if n_t else 0
        stats.append({
            "InIfName": inif,
            "ExporterName": exporter,
            "Min": min_bps / 1e9,
            "Max": max_bps / 1e9,
            "Last": last_bps / 1e9,
            "Average": avg_bps / 1e9,
            "P95": p95_bps / 1e9,
        })
    stats.sort(key=lambda x: (x["ExporterName"], x["InIfName"]))
    return series, stats, None


def _fmt_gbps(val: float) -> str:
    """Форматирование в Gbps как в UI Akvorado."""
    if val >= 1:
        return "{:.2f}Gbps".format(val)
    return "{:.2f}Mbps".format(val * 1000)


def _get_display_timezone(config: dict):
    """Читает display_timezone из конфига: GMT+5, UTC+5, Asia/Almaty и т.д. По умолчанию UTC."""
    tz_str = (config.get("display_timezone") or "UTC").strip()
    if not tz_str or tz_str.upper() in ("UTC", "UTC+0", "GMT+0"):
        return timezone.utc
    m = re.match(r"^(?:UTC|GMT)?([+-])(\d{1,2})(?::(\d{2}))?$", tz_str, re.I)
    if m:
        sign, h, mm = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        delta = timedelta(hours=h, minutes=mm) if sign == "+" else timedelta(hours=-h, minutes=-mm)
        return timezone(delta)
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_str)
    except Exception:
        return timezone.utc


def _tz_label(display_tz) -> str:
    """Подпись пояса для осей и подписи (GMT+5, UTC и т.д.)."""
    if display_tz == timezone.utc:
        return "UTC"
    utcoff = display_tz.utcoffset(None)
    if utcoff is None:
        return str(display_tz)
    sec = int(utcoff.total_seconds())
    h, r = divmod(abs(sec), 3600)
    m = r // 60
    sign = "+" if sec >= 0 else "-"
    return "GMT{}{}:{:02d}".format(sign, h, m) if m else "GMT{}{}".format(sign, h)


def _fmt_time_range(time_from: datetime, time_to: datetime, display_tz) -> str:
    """Формат времени среза в заданном поясе."""
    tz_label = _tz_label(display_tz)
    f = time_from.astimezone(display_tz)
    t = time_to.astimezone(display_tz)
    return "{} — {} {}".format(f.strftime("%d.%m %H:%M"), t.strftime("%d.%m %H:%M"), tz_label)


def build_graph_png_lines(
    series: dict[tuple[str, str], list[tuple[datetime, float]]],
    period_label: str,
    stats: list[dict],
    time_from: datetime,
    time_to: datetime,
    display_tz,
) -> io.BytesIO:
    """Строит график L3 (линии по интерфейсам) и таблицу Min/Max/Last/Avg/~95th в Gbps на одном изображении."""
    from matplotlib import gridspec
    time_range_str = _fmt_time_range(time_from, time_to, display_tz)
    tz_label = _tz_label(display_tz)
    n_table_rows = len(stats) + 1
    fig = plt.figure(figsize=(12, 4 + n_table_rows * 0.35))
    gs = gridspec.GridSpec(2, 1, height_ratios=[1.2, 0.8], hspace=0.35)
    ax = fig.add_subplot(gs[0])
    colors = plt.cm.tab10.colors
    for i, ((exporter, inif), points) in enumerate(sorted(series.items())):
        if not points:
            continue
        timestamps = [p[0] for p in points]
        gbps = [p[1] / 1e9 for p in points]
        label = "{} / {}".format(exporter, inif) if exporter else inif
        ax.plot(timestamps, gbps, color=colors[i % len(colors)], linewidth=1.2, label=label)
    ax.set_ylabel("Gbps (L3)")
    ax.set_xlabel("Время ({})".format(tz_label))
    ax.set_title("Трафик InIfBoundary = external, период: {}\nСрез: {}".format(period_label, time_range_str), fontsize=10)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M", tz=display_tz))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=25)
    ax.legend(loc="upper left", fontsize=7)
    ax.grid(True, linestyle="--", alpha=0.5)

    if stats:
        ax_t = fig.add_subplot(gs[1])
        ax_t.axis("off")
        col_labels = ["In If Name", "Exporter Name", "Min", "Max", "Last", "Average", "~95th"]
        cell_text = []
        for s in stats:
            cell_text.append([
                (s["InIfName"] or ""),
                (s["ExporterName"] or ""),
                _fmt_gbps(s["Min"]),
                _fmt_gbps(s["Max"]),
                _fmt_gbps(s["Last"]),
                _fmt_gbps(s["Average"]),
                _fmt_gbps(s["P95"]),
            ])
        table = ax_t.table(
            cellText=cell_text,
            colLabels=col_labels,
            loc="center",
            cellLoc="center",
            colColours=["#e0e0e0"] * 7,
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1, 1.8)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close()
    buf.seek(0)
    return buf


def format_stats_caption(period_label: str, time_from: datetime, time_to: datetime, display_tz) -> str:
    """Подпись: период и точное время среза (с — по) в заданном поясе."""
    return "Период: {}. Срез: {} L3".format(period_label, _fmt_time_range(time_from, time_to, display_tz))


def build_graph_for_period(config: dict, period_entry: tuple) -> tuple[io.BytesIO | None, str | None, str | None]:
    """Строит график за период по интерфейсам (InIfName, ExporterName). Возвращает (buf, caption, None) или (None, None, error_message)."""
    key, label, hours, interval_sec = period_entry
    display_tz = _get_display_timezone(config)
    ch_cfg = config.get("clickhouse", {})
    url = ch_cfg.get("url", "http://127.0.0.1:8123")
    boundary = ch_cfg.get("boundary_filter", "external")
    # Консоль Akvorado для разных периодов часто использует разные таблицы (1 ч — flows/flows_1m0s, 6 ч — flows_1m0s, 24/7 д — flows_5m0s)
    if hours >= 24 and ch_cfg.get("table_24h"):
        table = ch_cfg.get("table_24h")
        interval_sec = ch_cfg.get("interval_sec_24h", 300)
    elif hours >= 6 and ch_cfg.get("table_6h"):
        table = ch_cfg.get("table_6h")
        interval_sec = ch_cfg.get("interval_sec_6h", 60)
    else:
        table = ch_cfg.get("table", "default.flows")
    time_to = datetime.now(timezone.utc)
    time_from = time_to - timedelta(hours=hours)
    bytes_expr = ch_cfg.get("bytes_expression")
    series, stats, err = fetch_bps_by_interface(
        url, table, boundary, time_from, time_to, interval_sec,
        period_hours=hours, bytes_expression=bytes_expr,
    )
    if err:
        return None, None, err
    try:
        buf = build_graph_png_lines(series, label, stats, time_from, time_to, display_tz)
        caption = format_stats_caption(label, time_from, time_to, display_tz)
        return buf, caption, None
    except Exception as e:
        return None, None, "Ошибка построения графика: {}".format(e)


async def cmd_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда вида /graph_1h, /graph_6h, /graph_24h, /graph_7d — сразу отправить график."""
    if not update.message or not update.message.text:
        return
    cmd = (update.message.text or "").strip().split()[0].lower()
    if not cmd.startswith("/graph_"):
        return
    key = cmd.replace("/graph_", "", 1)
    entry = next((p for p in PERIODS if p[0] == key), None)
    if not entry:
        await update.message.reply_text("Неизвестный период.")
        return
    config = context.bot_data.get("config") or {}
    if not is_chat_allowed(config, update.effective_chat.id if update.effective_chat else None):
        await update.message.reply_text("Команда недоступна в этом чате.")
        return
    _, label, _, _ = entry
    status_msg = await update.message.reply_text("Строю график за {}…".format(label))
    buf, caption, err = build_graph_for_period(config, entry)
    if err:
        await status_msg.edit_text("Ошибка: {}".format(err))
        return
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=buf,
        caption=caption or "Период: {} (UTC).".format(label),
    )
    await status_msg.edit_text("График отправлен.")


async def cmd_graph(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = context.bot_data.get("config") or {}
    if not is_chat_allowed(config, update.effective_chat.id if update.effective_chat else None):
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
    if not is_chat_allowed(config, update.effective_chat.id if update.effective_chat else None):
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
    if not is_chat_allowed(config, update.effective_chat.id if update.effective_chat else None):
        await query.edit_message_text("Недоступно в этом чате.")
        return
    entry = next((p for p in PERIODS if p[0] == key), None)
    if not entry:
        await query.edit_message_text("Неизвестный период.")
        return
    _, label, _, _ = entry
    await query.edit_message_text("Строю график за {}…".format(label))
    buf, caption, err = build_graph_for_period(config, entry)
    if err:
        await query.edit_message_text("Ошибка: {}".format(err))
        return
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=buf,
        caption=caption or "Период: {} (UTC).".format(label),
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
    config.setdefault("clickhouse", {"url": "http://127.0.0.1:8123", "table": "default.flows", "boundary_filter": "external"})
    apply_env_overrides(config)

    token = (config.get("telegram") or {}).get("bot_token")
    if not token:
        print("В config.yaml задайте telegram.bot_token.", file=sys.stderr)
        return 1

    async def post_init(application: Application) -> None:
        """Меню бота: сразу периоды (нажал — получил график)."""
        await application.bot.set_my_commands([
            BotCommand("graph_1h", "1 ч"),
            BotCommand("graph_6h", "6 ч"),
            BotCommand("graph_24h", "24 ч"),
            BotCommand("graph_7d", "7 д"),
        ])
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )
    app.bot_data["config"] = config

    app.add_handler(CommandHandler("graph_1h", cmd_period))
    app.add_handler(CommandHandler("graph_6h", cmd_period))
    app.add_handler(CommandHandler("graph_24h", cmd_period))
    app.add_handler(CommandHandler("graph_7d", cmd_period))
    app.add_handler(CommandHandler("graph", cmd_graph))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_graph))
    app.add_handler(CallbackQueryHandler(on_period_callback))

    print("Бот запущен. В меню — периоды (1 ч, 6 ч, 24 ч, 7 д); нажал — график в чат.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
