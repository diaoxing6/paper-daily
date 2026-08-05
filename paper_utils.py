from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent


def load_config(path: str | Path = ROOT / "config.yaml") -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_json(path: str | Path, default: Any = None) -> Any:
    target = Path(path)
    if not target.exists():
        return default
    with target.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def compact_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_title(value: str | None) -> str:
    value = compact_space(value).casefold()
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE)


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip().replace("Z", "+00:00")
    for parser in (
        lambda item: datetime.fromisoformat(item),
        lambda item: datetime.strptime(item, "%Y-%m-%d"),
        lambda item: datetime.strptime(item, "%Y/%m/%d"),
        lambda item: datetime.strptime(item, "%Y %b %d"),
        lambda item: datetime.strptime(item, "%Y %b"),
        lambda item: datetime.strptime(item, "%Y"),
    ):
        try:
            result = parser(candidate)
            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)
            return result.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def iso_date(value: str | None) -> str:
    parsed = parse_date(value)
    return parsed.date().isoformat() if parsed else ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

