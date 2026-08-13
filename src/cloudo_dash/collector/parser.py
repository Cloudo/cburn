"""Разбор одной строки транскрипта JSONL (TZ §2, задача A1).

Чистая функция без обращений к БД и ФС: строка на входе, `ParsedRecord` или
`None` на выходе. Наружу не летит ни одного исключения — битая строка пишется
в лог и пропускается, offset обхода при этом двигается дальше.

Формат недокументирован и меняется между версиями Claude Code, поэтому разбор
терпимый: незнакомые поля игнорируются, незнакомые типы записей отдаются как
`RecordKind.UNKNOWN` с сырым payload (его складывает в `raw_events` индексатор).

Важное про модель данных, проверенное на реальной истории:

* **Запись ≠ ход.** Один ответ ассистента разложен по нескольким JSONL-записям —
  по одной на блок контента (`thinking`, `text`, каждый `tool_use`), и в каждой
  лежит *полный и одинаковый* `usage`. Ключ хода — `message_id`; суммировать
  usage по записям нельзя, иначе расход задваивается (в среднем ×4.6).
* **`uuid` не уникален по истории.** При resume Claude Code копирует прошлые
  ходы в новый файл с новым `session_id`, сохраняя `uuid` и `message_id`
  (встречались копии одного хода в 20 файлах). Склейка — забота индексатора.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import orjson

log = logging.getLogger(__name__)

#: Типы записей, которые разбираются в структуру. Всё остальное — UNKNOWN.
_ASSISTANT = "assistant"
_USER = "user"

#: Название сессии. `custom-title` задан человеком и важнее сгенерированного
#: `ai-title`; у обеих записей нет ни timestamp, ни uuid — только sessionId.
_TITLE_FIELDS = {"ai-title": "aiTitle", "custom-title": "customTitle"}

#: Последний промпт сессии Claude Code пишет отдельной записью — искать его
#: перебором user-записей не нужно.
_LAST_PROMPT = "last-prompt"

#: Ограничение на текст промпта: в БД он нужен только как подпись сессии.
PROMPT_LIMIT = 200


class RecordKind(StrEnum):
    """Смысловой класс записи — то, чем она является для учёта расхода."""

    ASSISTANT = "assistant"  # ответ модели (или его блок), несёт usage
    PROMPT = "prompt"  # настоящий промпт пользователя
    TOOL_RESULT = "tool_result"  # результат инструмента, приходит записью user
    TITLE = "title"  # название сессии: ai-title или custom-title
    LAST_PROMPT = "last_prompt"  # последний промпт сессии, отдельной записью
    UNKNOWN = "unknown"  # всё прочее — в raw_events


@dataclass(frozen=True, slots=True)
class Usage:
    """Расход одного хода. Записи в 5m- и 1h-кэш тарифицируются по-разному."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0

    @property
    def cache_write(self) -> int:
        """Суммарная запись в кэш."""
        return self.cache_write_5m + self.cache_write_1h

    @property
    def context_estimate(self) -> int:
        """Оценка занятого окна контекста на момент хода (TZ §4)."""
        return self.input_tokens + self.cache_read + self.cache_write

    def merge(self, other: Usage) -> Usage:
        """Свести usage двух записей одного хода — поэлементным максимумом.

        Записи одного ответа обычно несут одинаковый usage, но у 2 527 ходов из
        15 197 в реальной истории часть записей нулевая: расход проставляется по
        завершении ответа, а промежуточные блоки уже записаны. Максимум даёт
        финальное значение независимо от порядка чтения; сумма завысила бы расход
        в разы, первая запись — занизила бы на треть.
        """
        return Usage(
            input_tokens=max(self.input_tokens, other.input_tokens),
            output_tokens=max(self.output_tokens, other.output_tokens),
            cache_read=max(self.cache_read, other.cache_read),
            cache_write_5m=max(self.cache_write_5m, other.cache_write_5m),
            cache_write_1h=max(self.cache_write_1h, other.cache_write_1h),
        )


@dataclass(frozen=True, slots=True)
class ToolUse:
    """Вызов инструмента из блока `tool_use`."""

    tool: str
    tool_use_id: str | None = None
    detail: str | None = None  # для Bash — нормализованная команда


@dataclass(frozen=True, slots=True)
class ParsedRecord:
    """Разобранная строка транскрипта."""

    kind: RecordKind
    raw_type: str
    uuid: str | None = None
    parent_uuid: str | None = None
    session_id: str | None = None
    ts: str | None = None
    version: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    is_sidechain: bool = False
    # ASSISTANT
    message_id: str | None = None
    request_id: str | None = None
    model: str | None = None
    stop_reason: str | None = None
    usage: Usage | None = None
    tools: tuple[ToolUse, ...] = ()
    # PROMPT
    prompt_text: str | None = None
    prompt_source: str | None = None
    is_compact_summary: bool = False
    # TITLE
    title: str | None = None
    title_source: str | None = None  # ai | custom
    # UNKNOWN
    payload: dict[str, Any] = field(default_factory=dict)


def parse_line(raw: str) -> ParsedRecord | None:
    """Разобрать строку транскрипта. Битая или пустая строка → `None`."""
    line = raw.strip()
    if not line:
        return None
    try:
        record = orjson.loads(line)
    except orjson.JSONDecodeError as exc:
        log.warning("нечитаемая строка транскрипта: %s", exc)
        return None
    if not isinstance(record, dict):
        log.warning("строка транскрипта не объект: %s", type(record).__name__)
        return None
    try:
        return _parse_record(record)
    except Exception as exc:  # разбор не должен останавливать обход файла
        log.warning("строка транскрипта не разобрана (%s): %s", record.get("type"), exc)
        return None


def _parse_record(record: dict[str, Any]) -> ParsedRecord:
    raw_type = _str(record.get("type")) or ""
    common: dict[str, Any] = {
        "raw_type": raw_type,
        "uuid": _str(record.get("uuid")),
        "parent_uuid": _str(record.get("parentUuid")),
        "session_id": _str(record.get("sessionId")),
        "ts": _str(record.get("timestamp")),
        "version": _str(record.get("version")),
        "cwd": _str(record.get("cwd")),
        "git_branch": _str(record.get("gitBranch")),
        "is_sidechain": bool(record.get("isSidechain")),
    }
    message = record.get("message")
    if not isinstance(message, dict):
        message = {}

    if raw_type == _ASSISTANT:
        return _parse_assistant(record, message, common)
    if raw_type == _USER:
        return _parse_user(record, message, common)
    if raw_type == _LAST_PROMPT:
        text = _str(record.get("lastPrompt"))
        if text:
            return ParsedRecord(
                kind=RecordKind.LAST_PROMPT,
                prompt_text=_clean_prompt(text),
                **common,
            )
    if raw_type in _TITLE_FIELDS:
        title = _str(record.get(_TITLE_FIELDS[raw_type]))
        if title:
            return ParsedRecord(
                kind=RecordKind.TITLE,
                title=title,
                title_source=raw_type.removesuffix("-title"),
                **common,
            )
    return ParsedRecord(kind=RecordKind.UNKNOWN, payload=record, **common)


def _parse_assistant(
    record: dict[str, Any], message: dict[str, Any], common: dict[str, Any]
) -> ParsedRecord:
    return ParsedRecord(
        kind=RecordKind.ASSISTANT,
        message_id=_str(message.get("id")),
        request_id=_str(record.get("requestId")),
        model=_str(message.get("model")),
        stop_reason=_str(message.get("stop_reason")),
        usage=_parse_usage(message.get("usage")),
        tools=_parse_tools(message.get("content")),
        **common,
    )


def _parse_user(
    record: dict[str, Any], message: dict[str, Any], common: dict[str, Any]
) -> ParsedRecord:
    """Промпт против результата инструмента.

    Различать по `promptSource` нельзя: поле есть меньше чем у 5% user-записей
    (значения `sdk`, `typed`), у набранных руками промптов его обычно нет.
    Надёжный признак — блок `tool_result` в контенте.
    """
    content = message.get("content")
    if _is_tool_result(content):
        return ParsedRecord(kind=RecordKind.TOOL_RESULT, **common)
    return ParsedRecord(
        kind=RecordKind.PROMPT,
        prompt_text=_prompt_text(content),
        prompt_source=_str(record.get("promptSource")),
        is_compact_summary=bool(record.get("isCompactSummary")),
        **common,
    )


def _is_tool_result(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)


#: Служебные блоки, которые Claude Code подмешивает к промпту человека:
#: контекст IDE, напоминания системы, предупреждения слэш-команд. Для подписи
#: сессии они бесполезны — настоящий вопрос стоит после них.
_SERVICE_BLOCK = re.compile(r"<([a-z][\w-]*)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_SERVICE_OPEN = re.compile(r"^\s*<[a-z][\w-]*>", re.IGNORECASE)
_COMMAND_NAME = re.compile(r"<command-name>\s*(.+?)\s*</command-name>", re.DOTALL | re.IGNORECASE)


def _strip_service_blocks(text: str) -> str:
    """Оставить от промпта то, что написал человек.

    Запуск слэш-команды выглядит как предупреждение `local-command-caveat`
    и блоки `command-name`/`command-message` — живого текста там нет вовсе,
    и подписью сессии становится сама команда.
    """
    stripped = _SERVICE_BLOCK.sub(" ", text).strip()
    if stripped:
        return stripped
    command = _COMMAND_NAME.search(text)
    if command:
        return command.group(1)
    # Один служебный текст без содержания: пусть подписью станет название
    # сессии или проект — стена служебных предупреждений не подпись.
    return ""


def _clean_prompt(text: str) -> str | None:
    """Обрезать промпт и убрать служебные обёртки."""
    text = text.strip()
    if _SERVICE_OPEN.match(text):
        text = _strip_service_blocks(text)
    return text[:PROMPT_LIMIT] or None


def _prompt_text(content: Any) -> str | None:
    """Текст промпта, обрезанный до `PROMPT_LIMIT`. Вложения пропускаются."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = [
            _str(block.get("text")) or ""
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(part for part in parts if part)
    else:
        return None
    return _clean_prompt(text)


def _parse_usage(usage: Any) -> Usage | None:
    """Разбор `message.usage`.

    Разбивка записи в кэш живёт в `cache_creation`; у старых версий и у
    синтетических записей её может не быть — тогда всё считается 5-минутной,
    чтобы сумма всё равно сходилась с `cache_creation_input_tokens`.
    """
    if not isinstance(usage, dict):
        return None
    creation = usage.get("cache_creation")
    if isinstance(creation, dict):
        write_5m = _int(creation.get("ephemeral_5m_input_tokens"))
        write_1h = _int(creation.get("ephemeral_1h_input_tokens"))
    else:
        write_5m = _int(usage.get("cache_creation_input_tokens"))
        write_1h = 0
    return Usage(
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        cache_read=_int(usage.get("cache_read_input_tokens")),
        cache_write_5m=write_5m,
        cache_write_1h=write_1h,
    )


def _parse_tools(content: Any) -> tuple[ToolUse, ...]:
    if not isinstance(content, list):
        return ()
    tools: list[ToolUse] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = _str(block.get("name"))
        if not name:
            continue
        tools.append(
            ToolUse(
                tool=name,
                tool_use_id=_str(block.get("id")),
                detail=_tool_detail(name, block.get("input")),
            )
        )
    return tuple(tools)


def _tool_detail(tool: str, tool_input: Any) -> str | None:
    """Деталь вызова для профиля инструментов (TZ §4).

    Только для Bash и только нормализованная команда — приватность требует, чтобы
    в БД не оседали ни аргументы, ни пути (TZ §7).
    """
    if tool != "Bash" or not isinstance(tool_input, dict):
        return None
    return normalize_command(_str(tool_input.get("command")))


#: Токены, после которых начинается новая команда: нормализуется только первая.
_COMMAND_SEPARATORS = ("|", "&&", "||", ";", "\n")


def normalize_command(command: str | None) -> str | None:
    """Свернуть bash-команду до «первое слово + подкоманда».

    `git commit -m "..."` → `git commit`, `sed -n 1,50p f.py` → `sed`,
    `cd /x && npm run build` → `cd`. Аргументы и пути отбрасываются.
    """
    if not command:
        return None
    head = command.strip()
    for separator in _COMMAND_SEPARATORS:
        head = head.split(separator, 1)[0]
    tokens = head.split()
    if not tokens:
        return None
    name = tokens[0].rsplit("/", 1)[-1]
    if not name:
        return None
    if len(tokens) > 1 and _looks_like_subcommand(tokens[1]):
        return f"{name} {tokens[1]}"
    return name


def _looks_like_subcommand(token: str) -> bool:
    return (
        len(token) <= 20
        and not token.startswith("-")
        and all(char.isalnum() or char in "-_" for char in token)
        and any(char.isalpha() for char in token)
    )


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
