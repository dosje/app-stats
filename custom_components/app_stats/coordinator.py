"""Fetch store reports, cache them in Home Assistant storage and build the totals."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AppleClient, GoogleClient, StoreAuthError, StoreError
from .const import (
    APPLE_CONCURRENCY,
    APPLE_MONTHS_BACK,
    APPLE_RECHECK_DAYS,
    CONF_APPLE_ISSUER_ID,
    CONF_APPLE_KEY_ID,
    CONF_APPLE_PRIVATE_KEY,
    CONF_APPLE_VENDOR,
    CONF_GOOGLE_BUCKET,
    CONF_GOOGLE_SERVICE_ACCOUNT,
    DOMAIN,
    OPT_INCLUDE_REDOWNLOADS,
    STORAGE_VERSION,
    UPDATE_INTERVAL,
)
from .parsers import GOOGLE_OVERVIEW_RE, parse_apple_sales_report, parse_google_overview

_LOGGER = logging.getLogger(__name__)


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _prev_month(d: date) -> date:
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def apple_plan(today: date) -> tuple[list[date], list[date]]:
    """Daily dates (within Apple's 365-day window, whole months) + older months for monthly reports."""
    edge = today - timedelta(days=364)
    daily_start = _month_start(edge) if edge.day == 1 else (_month_start(edge) + timedelta(days=32)).replace(day=1)
    days = [daily_start + timedelta(days=i) for i in range((today - daily_start).days)]  # up to yesterday
    months: list[date] = []
    m = _prev_month(daily_start)
    for _ in range(APPLE_MONTHS_BACK):
        months.append(m)
        m = _prev_month(m)
    return days, months


class AppStatsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keeps all store numbers."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL, config_entry=entry)
        session = async_get_clientsession(hass)
        d = entry.data
        self.apple: AppleClient | None = None
        self.google: GoogleClient | None = None
        if d.get(CONF_APPLE_ISSUER_ID) and d.get(CONF_APPLE_PRIVATE_KEY):
            self.apple = AppleClient(session, d[CONF_APPLE_ISSUER_ID], d[CONF_APPLE_KEY_ID], d[CONF_APPLE_VENDOR], d[CONF_APPLE_PRIVATE_KEY])
        if d.get(CONF_GOOGLE_BUCKET) and d.get(CONF_GOOGLE_SERVICE_ACCOUNT):
            self.google = GoogleClient(session, d[CONF_GOOGLE_BUCKET], d[CONF_GOOGLE_SERVICE_ACCOUNT])
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._cache: dict[str, Any] | None = None

    async def _load_cache(self) -> dict[str, Any]:
        if self._cache is None:
            self._cache = await self._store.async_load() or {}
            self._cache.setdefault("apple", {})
            self._cache.setdefault("google", {})
        return self._cache

    # ---------------- Apple ----------------
    async def _apple(self, cache: dict[str, Any]) -> dict[str, Any]:
        assert self.apple
        today = dt_util.now().date()
        days, months = apple_plan(today)
        ac: dict[str, Any] = cache["apple"]
        todo: list[tuple[str, str, str]] = []
        for d in days:
            key = f"D:{d.isoformat()}"
            entry = ac.get(key)
            if entry is None or (entry.get("empty") and (today - d).days <= APPLE_RECHECK_DAYS):
                todo.append((key, "DAILY", d.isoformat()))
        for m in months:
            key = f"M:{m.strftime('%Y-%m')}"
            if key not in ac:
                todo.append((key, "MONTHLY", m.strftime("%Y-%m")))

        sem = asyncio.Semaphore(APPLE_CONCURRENCY)

        async def fetch(key: str, freq: str, when: str) -> None:
            async with sem:
                raw = await self.apple.sales_report(freq, when)
            ac[key] = {"empty": True} if raw is None else {"apps": parse_apple_sales_report(raw)}

        if todo:
            _LOGGER.debug("Apple: fetching %d reports", len(todo))
            results = await asyncio.gather(*(fetch(*t) for t in todo), return_exceptions=True)
            for r in results:
                if isinstance(r, StoreAuthError):
                    raise r
            errors = [r for r in results if isinstance(r, Exception)]
            if errors:
                _LOGGER.warning("Apple: %d reports failed, first: %s", len(errors), errors[0])

        valid = {f"D:{d.isoformat()}" for d in days} | {f"M:{m.strftime('%Y-%m')}" for m in months}
        for key in [k for k in ac if k not in valid]:
            del ac[key]  # outside the plan (e.g. daily data now covered by a monthly report)

        apps: dict[str, dict] = {}
        last_day: str | None = None
        for key, entry in ac.items():
            for app_id, v in (entry.get("apps") or {}).items():
                a = apps.setdefault(app_id, {"title": v["title"], "installs": 0, "updates": 0, "redownloads": 0})
                a["title"] = v["title"] or a["title"]
                for k in ("installs", "updates", "redownloads"):
                    a[k] += v[k]
            if key.startswith("D:") and "apps" in entry and (last_day is None or key[2:] > last_day):
                last_day = key[2:]
        for app_id, a in apps.items():
            day = (ac.get(f"D:{last_day}", {}).get("apps") or {}).get(app_id, {}) if last_day else {}
            a["last_day_installs"] = day.get("installs", 0)
            a["last_day_updates"] = day.get("updates", 0)
        oldest = min((k[2:] for k, e in ac.items() if k.startswith("M:") and "apps" in e), default=None)
        return {"apps": apps, "last_day": last_day, "oldest_month_with_data": oldest, "months_checked": len(months)}

    # ---------------- Google ----------------
    async def _google(self, cache: dict[str, Any]) -> dict[str, Any]:
        assert self.google
        gc: dict[str, Any] = cache["google"]
        objects = await self.google.list_install_reports()
        seen = set()
        for obj in objects:
            m = GOOGLE_OVERVIEW_RE.match(obj["name"])
            if not m:
                continue
            seen.add(obj["name"])
            cached = gc.get(obj["name"])
            if cached and str(cached.get("generation")) == str(obj.get("generation")):
                continue
            raw = await self.google.download(obj["name"])
            gc[obj["name"]] = {"generation": obj.get("generation"), "package": m.group(1), "month": m.group(2), "data": parse_google_overview(raw)}
        for name in [n for n in gc if n not in seen]:
            del gc[name]

        apps: dict[str, dict] = {}
        for item in sorted(gc.values(), key=lambda i: i["month"]):
            d = item["data"]
            if not d:
                continue
            a = apps.setdefault(item["package"], {"installs_sum": 0, "updates": 0, "total_user_installs": None, "active_installs": None, "last_date": None})
            a["installs_sum"] += d["installs"]
            a["updates"] += d["updates"]
            if d["total_user_installs"] is not None:
                a["total_user_installs"] = d["total_user_installs"]
            if d["active_installs"] is not None:
                a["active_installs"] = d["active_installs"]
            if a["last_date"] is None or d["last_date"] >= a["last_date"]:
                a["last_date"] = d["last_date"]
                a["last_day_installs"] = d["last_day_installs"]
                a["last_day_updates"] = d["last_day_updates"]
        for a in apps.values():
            a["installs"] = a["total_user_installs"] if a["total_user_installs"] is not None else a["installs_sum"]
        return {"apps": apps}

    # ---------------- combine ----------------
    async def _async_update_data(self) -> dict[str, Any]:
        cache = await self._load_cache()
        data: dict[str, Any] = {"apple": None, "google": None, "errors": {}}
        try:
            if self.apple:
                try:
                    data["apple"] = await self._apple(cache)
                except StoreError as err:
                    data["errors"]["apple"] = str(err)
            if self.google:
                try:
                    data["google"] = await self._google(cache)
                except StoreError as err:
                    data["errors"]["google"] = str(err)
        except StoreAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        finally:
            await self._store.async_save(cache)
        if data["apple"] is None and data["google"] is None:
            raise UpdateFailed("; ".join(data["errors"].values()) or "Keine Store-Daten")
        incl = self.config_entry.options.get(OPT_INCLUDE_REDOWNLOADS, False)
        if data["apple"]:
            for a in data["apple"]["apps"].values():
                a["installs_effective"] = a["installs"] + (a["redownloads"] if incl else 0)
        data["updated"] = dt_util.utcnow().isoformat()
        return data
