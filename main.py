from __future__ import annotations

import asyncio
from collections import deque
import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
import os
import tempfile

import httpx
import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .pixiv_random_core import PixivImageEntry, filter_candidates, normalize_pixiv_entries, pick_random_entry


@register(
    "astrbot_plugin_pixiv_random",
    "Enter & Codex",
    "从 PixivCollection 静态元数据中随机抽取图片并发送。",
    "0.1.0",
    "https://pixivcollection.pages.dev/",
)
class PixivRandomPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config or {}
        self._client: Optional[httpx.AsyncClient] = None
        self._refresh_task: Optional[asyncio.Task[Any]] = None
        self._refresh_lock = asyncio.Lock()
        self._entries: list[PixivImageEntry] = []
        self._last_refresh_at: Optional[datetime] = None
        self._last_refresh_error: str = "插件尚未完成首次加载。"
        self._runtime_safe_mode = self._get_bool("safe_mode", True)
        self._recent_sent: deque[str] = deque(maxlen=self._recent_avoid_count)

    @property
    def _data_url(self) -> str:
        return str(self.config.get("data_url", "")).strip()

    @property
    def _preview_base_url(self) -> str:
        return str(self.config.get("preview_base_url", "")).strip()

    @property
    def _original_base_url(self) -> str:
        return str(self.config.get("original_base_url", "")).strip()

    @property
    def _site_url(self) -> str:
        return str(self.config.get("site_url", "")).strip()

    @property
    def _safe_mode_enabled(self) -> bool:
        return self._runtime_safe_mode

    @property
    def _max_sanity_level(self) -> int:
        return max(0, self._get_int("max_sanity_level", 4))

    @property
    def _refresh_interval_minutes(self) -> int:
        return max(1, self._get_int("refresh_interval_minutes", 60))

    @property
    def _recent_avoid_count(self) -> int:
        return max(0, self._get_int("recent_avoid_count", 20))

    @property
    def _send_mode(self) -> str:
        mode = str(self.config.get("send_mode", "remote_first")).strip().lower()
        return mode if mode in {"remote_first", "download_first"} else "remote_first"

    @property
    def _allowed_groups(self) -> set[str]:
        raw = self.config.get("allowed_groups", [])
        if isinstance(raw, str):
            raw = [segment.strip() for segment in raw.split(",") if segment.strip()]
        if not isinstance(raw, list):
            return set()
        return {str(group).strip() for group in raw if str(group).strip()}

    @property
    def _admin_can_disable_safe_mode(self) -> bool:
        return self._get_bool("admin_can_disable_safe_mode", True)

    def _get_int(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    def _get_bool(self, key: str, default: bool) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "开"}
        return bool(value)

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": "astrbot-plugin-pixiv-random/0.1.0"},
            timeout=httpx.Timeout(20.0),
            follow_redirects=True,
        )

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    async def _request_json(self) -> Any:
        if not self._data_url:
            raise RuntimeError("未配置 data_url。")
        if not self._preview_base_url:
            raise RuntimeError("未配置 preview_base_url。")

        client = await self._ensure_client()
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                response = await client.get(self._data_url)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                await asyncio.sleep(1 + attempt)
        raise RuntimeError(f"拉取元数据失败: {last_error}") from last_error

    async def _refresh_entries(self, reason: str) -> tuple[bool, str]:
        async with self._refresh_lock:
            try:
                payload = await self._request_json()
                entries = normalize_pixiv_entries(payload, self._preview_base_url, self._original_base_url)
                if not entries:
                    raise RuntimeError("元数据为空，未解析到任何图片条目。")

                self._entries = entries
                self._last_refresh_at = datetime.now()
                self._last_refresh_error = ""
                self._recent_sent = deque(self._recent_sent, maxlen=self._recent_avoid_count)
                message = f"{reason}刷新成功，共加载 {len(entries)} 条图片。"
                logger.info(f"[pixiv_random] {message}")
                return True, message
            except Exception as exc:  # noqa: BLE001
                self._last_refresh_error = str(exc)
                logger.error(f"[pixiv_random] {reason}刷新失败: {exc}")
                return False, f"{reason}刷新失败: {exc}"

    async def _refresh_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._refresh_interval_minutes * 60)
                await self._refresh_entries("定时")
        except asyncio.CancelledError:
            logger.info("[pixiv_random] 定时刷新任务已停止。")
            raise

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self) -> None:
        await self._refresh_entries("启动")
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def terminate(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresh_task
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _extract_group_id(self, event: AstrMessageEvent) -> str:
        possible_values: list[Any] = []
        for name in ("get_group_id", "group_id"):
            target = getattr(event, name, None)
            if callable(target):
                with contextlib.suppress(Exception):
                    possible_values.append(target())
            elif target is not None:
                possible_values.append(target)

        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            for attr in ("group_id", "group_openid", "channel_id"):
                value = getattr(message_obj, attr, None)
                if value is not None:
                    possible_values.append(value)

        for value in possible_values:
            text = str(value).strip()
            if text:
                return text
        return ""

    def _is_group_allowed(self, event: AstrMessageEvent) -> bool:
        allowed_groups = self._allowed_groups
        if not allowed_groups:
            return True
        group_id = self._extract_group_id(event)
        if not group_id:
            return True
        return group_id in allowed_groups

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        possible_values: list[Any] = []
        for name in ("is_admin", "is_admin_user"):
            target = getattr(event, name, None)
            if callable(target):
                with contextlib.suppress(Exception):
                    result = target()
                    if isinstance(result, bool):
                        return result
            elif isinstance(target, bool):
                return target

        for attr in ("role",):
            value = getattr(event, attr, None)
            if value is not None:
                possible_values.append(value)

        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            sender = getattr(message_obj, "sender", None)
            for container in (message_obj, sender):
                if container is None:
                    continue
                for attr in ("role", "permission", "card"):
                    value = getattr(container, attr, None)
                    if value is not None:
                        possible_values.append(value)

        normalized = {str(value).strip().lower() for value in possible_values if str(value).strip()}
        admin_markers = {"admin", "administrator", "owner", "group_owner", "manage"}
        return bool(normalized & admin_markers)

    def _build_caption(self, entry: PixivImageEntry) -> str:
        lines = [
            f"标题：{entry.title}",
            f"作者：{entry.author_name} ({entry.author_id})" if entry.author_id else f"作者：{entry.author_name}",
            f"PID：{entry.id}",
            f"Pixiv：{entry.pixiv_artwork_url}",
        ]
        if self._site_url:
            lines.append(f"站点：{self._site_url}")
        return "\n".join(lines)

    async def _download_to_temp(self, url: str) -> str:
        client = await self._ensure_client()
        response = await client.get(url)
        response.raise_for_status()
        suffix = Path(urlparse(url).path).suffix or ".img"
        fd, tmp_path = tempfile.mkstemp(prefix="pixiv_random_", suffix=suffix)
        os.close(fd)
        path = Path(tmp_path)
        path.write_bytes(response.content)
        return str(path)

    async def _send_remote(self, event: AstrMessageEvent, entry: PixivImageEntry) -> None:
        chain = [Comp.Image.fromURL(entry.preview_url), Comp.Plain("\n" + self._build_caption(entry))]
        await event.send(event.chain_result(chain))

    async def _send_local(self, event: AstrMessageEvent, entry: PixivImageEntry) -> None:
        tmp_path = ""
        try:
            try:
                tmp_path = await self._download_to_temp(entry.preview_url)
            except Exception:  # noqa: BLE001
                if not entry.original_url:
                    raise
                tmp_path = await self._download_to_temp(entry.original_url)
            chain = [Comp.Image.fromFileSystem(tmp_path), Comp.Plain("\n" + self._build_caption(entry))]
            await event.send(event.chain_result(chain))
        finally:
            if tmp_path:
                with contextlib.suppress(OSError):
                    Path(tmp_path).unlink()

    async def _send_entry(self, event: AstrMessageEvent, entry: PixivImageEntry) -> None:
        if self._send_mode == "download_first":
            await self._send_local(event, entry)
            return

        try:
            await self._send_remote(event, entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[pixiv_random] 远程 URL 发送失败，开始回退本地发送: {exc}")
            await self._send_local(event, entry)

    async def _pick_and_send_entry(self, event: AstrMessageEvent) -> tuple[bool, str, Optional[PixivImageEntry]]:
        if not self._entries:
            return False, f"图库数据未就绪。{self._last_refresh_error or '请稍后再试。'}", None

        entry = self._pick_candidate()
        if entry is None:
            return False, "当前没有可发送的图片，请检查安全模式、缓存或最近去重设置。", None

        try:
            await self._send_entry(event, entry)
            self._recent_sent.append(entry.key)
            logger.info(f"[pixiv_random] 已发送图片 {entry.key}")
            return True, f"已发送随机图片：{entry.id}", entry
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[pixiv_random] 发送图片失败: {exc}")
            return False, f"发送图片失败：{exc}", None

    def _pick_candidate(self) -> Optional[PixivImageEntry]:
        recent_keys = set(self._recent_sent)
        candidates = filter_candidates(
            self._entries,
            safe_mode=self._safe_mode_enabled,
            max_sanity_level=self._max_sanity_level,
            recent_keys=recent_keys,
        )
        if not candidates and recent_keys:
            candidates = filter_candidates(
                self._entries,
                safe_mode=self._safe_mode_enabled,
                max_sanity_level=self._max_sanity_level,
                recent_keys=set(),
            )
        return pick_random_entry(candidates)

    @filter.command("收藏", alias={"pixiv", "来张图"})
    async def random_image(self, event: AstrMessageEvent):
        if not self._is_group_allowed(event):
            return
        ok, message, _entry = await self._pick_and_send_entry(event)
        if not ok:
            yield event.plain_result(message)

    @filter.llm_tool(name="pixiv_random_image")
    async def pixiv_random_image_tool(self, event: AstrMessageEvent, request: str = ""):
        """从 Pixiv 收藏图库中随机发送一张图片到当前会话。

        适用于用户希望“来一张 pixiv 图”“随机发张收藏图”“随便来张图”的场景。
        当前工具只会随机抽取图片，不会按标签、作者或风格精确筛选。

        Args:
            request(string): 用户对这次发图的自然语言要求，仅用于帮助模型判断是否应调用本工具。
        """
        if not self._is_group_allowed(event):
            yield event.plain_result("当前会话不在插件允许响应的范围内。")
            return

        ok, message, entry = await self._pick_and_send_entry(event)
        if not ok:
            yield event.plain_result(message)
            return

        if entry is None:
            yield event.plain_result("图片已发送。")
            return

        yield event.plain_result(
            f"已向当前会话发送一张随机图片。标题：{entry.title}；作者：{entry.author_name}；PID：{entry.id}。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("收藏刷新")
    async def refresh_command(self, event: AstrMessageEvent):
        success, message = await self._refresh_entries("手动")
        if success:
            yield event.plain_result(message)
            return
        yield event.plain_result(message)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("收藏状态")
    async def status_command(self, event: AstrMessageEvent):
        last_refresh = self._last_refresh_at.strftime("%Y-%m-%d %H:%M:%S") if self._last_refresh_at else "未刷新"
        status_lines = [
            f"图库条目数：{len(self._entries)}",
            f"上次刷新：{last_refresh}",
            f"安全模式：{'开启' if self._safe_mode_enabled else '关闭'}",
            f"最大不健全度：{self._max_sanity_level}",
            f"发送模式：{self._send_mode}",
            f"最近去重窗口：{self._recent_avoid_count}",
        ]
        if self._last_refresh_error:
            status_lines.append(f"最近错误：{self._last_refresh_error}")
        yield event.plain_result("\n".join(status_lines))

    @filter.command("收藏安全")
    async def safe_mode_command(self, event: AstrMessageEvent, action: str = ""):
        if not self._admin_can_disable_safe_mode:
            yield event.plain_result("当前插件配置未允许运行时切换安全模式。")
            return
        if not self._is_admin(event):
            yield event.plain_result("只有管理员才能切换安全模式。")
            return

        action = action.strip()
        if action not in {"开", "关"}:
            yield event.plain_result(f"当前安全模式：{'开启' if self._safe_mode_enabled else '关闭'}。用法：/收藏安全 开|关")
            return

        self._runtime_safe_mode = action == "开"
        yield event.plain_result(f"安全模式已{'开启' if self._runtime_safe_mode else '关闭'}。重启插件后会恢复为配置中的默认值。")
