from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional
import random


def _join_url(base_url: str, name: str) -> str:
    return f"{base_url.rstrip('/')}/{name.lstrip('/')}"


@dataclass(frozen=True)
class PixivImageEntry:
    id: int
    part: int
    title: str
    author_name: str
    author_id: int
    ext: str
    sanity_level: int
    x_restrict: int
    bookmark: int
    created_at: str
    preview_url: str
    original_url: str
    pixiv_artwork_url: str

    @property
    def key(self) -> str:
        return f"{self.id}:{self.part}"


def _normalize_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def normalize_pixiv_entries(
    payload: Any,
    preview_base_url: str,
    original_base_url: str = "",
) -> list[PixivImageEntry]:
    if not isinstance(payload, list):
        raise ValueError("Pixiv metadata payload must be a list.")

    preview_base_url = preview_base_url.strip()
    original_base_url = original_base_url.strip()
    if not preview_base_url:
        raise ValueError("preview_base_url is required.")

    entries: list[PixivImageEntry] = []
    for item in payload:
        if not isinstance(item, dict):
            continue

        image_id = _normalize_int(item.get("id"), -1)
        if image_id <= 0:
            continue

        part = _normalize_int(item.get("part"), 0)
        ext = _normalize_str(item.get("ext"), "jpg").lower()
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        title = _normalize_str(item.get("title"), "未命名作品")

        preview_name = f"{image_id}_p{part}.webp"
        original_name = f"{image_id}_p{part}.{ext}"
        preview_url = _join_url(preview_base_url, preview_name)
        original_url = _join_url(original_base_url, original_name) if original_base_url else ""

        entries.append(
            PixivImageEntry(
                id=image_id,
                part=part,
                title=title,
                author_name=_normalize_str(author.get("name"), "未知作者"),
                author_id=_normalize_int(author.get("id"), 0),
                ext=ext,
                sanity_level=_normalize_int(item.get("sanity_level"), 0),
                x_restrict=_normalize_int(item.get("x_restrict"), 0),
                bookmark=_normalize_int(item.get("bookmark"), 0),
                created_at=_normalize_str(item.get("created_at"), ""),
                preview_url=preview_url,
                original_url=original_url,
                pixiv_artwork_url=f"https://www.pixiv.net/artworks/{image_id}",
            )
        )

    return entries


def filter_candidates(
    entries: Iterable[PixivImageEntry],
    *,
    safe_mode: bool,
    max_sanity_level: int,
    recent_keys: Optional[set[str]] = None,
) -> list[PixivImageEntry]:
    recent_keys = recent_keys or set()
    candidates: list[PixivImageEntry] = []
    for entry in entries:
        if entry.key in recent_keys:
            continue
        if safe_mode and (entry.x_restrict > 0 or entry.sanity_level > max_sanity_level):
            continue
        candidates.append(entry)
    return candidates


def pick_random_entry(
    entries: list[PixivImageEntry],
    *,
    rng: Optional[random.Random] = None,
) -> Optional[PixivImageEntry]:
    if not entries:
        return None
    chooser = rng or random.Random()
    return chooser.choice(entries)
