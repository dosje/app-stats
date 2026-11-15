"""Sensors: totals per app and platform, per linked app (both stores) and over all apps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AppStatsConfigEntry
from .const import DOMAIN, OPT_LINKS
from .coordinator import AppStatsCoordinator


@dataclass(frozen=True, kw_only=True)
class StatDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]


TOTAL = SensorStateClass.TOTAL
DAY = SensorStateClass.MEASUREMENT

APPLE_SENSORS = (
    StatDescription(key="installs_total", translation_key="installs_total", icon="mdi:download", state_class=TOTAL, value=lambda a: a.get("installs_effective", a["installs"])),
    StatDescription(key="updates_total", translation_key="updates_total", icon="mdi:update", state_class=TOTAL, value=lambda a: a["updates"]),
    StatDescription(key="redownloads_total", translation_key="redownloads_total", icon="mdi:download-multiple", state_class=TOTAL, value=lambda a: a["redownloads"]),
    StatDescription(key="installs_last_day", translation_key="installs_last_day", icon="mdi:calendar-today", state_class=DAY, value=lambda a: a["last_day_installs"]),
    StatDescription(key="updates_last_day", translation_key="updates_last_day", icon="mdi:calendar-sync", state_class=DAY, value=lambda a: a["last_day_updates"]),
)
GOOGLE_SENSORS = (
    StatDescription(key="installs_total", translation_key="installs_total", icon="mdi:download", state_class=TOTAL, value=lambda a: a["installs"]),
    StatDescription(key="updates_total", translation_key="updates_total", icon="mdi:update", state_class=TOTAL, value=lambda a: a["updates"]),
    StatDescription(key="active_installs", translation_key="active_installs", icon="mdi:cellphone-check", state_class=DAY, value=lambda a: a["active_installs"]),
    StatDescription(key="installs_last_day", translation_key="installs_last_day", icon="mdi:calendar-today", state_class=DAY, value=lambda a: a.get("last_day_installs")),
    StatDescription(key="updates_last_day", translation_key="updates_last_day", icon="mdi:calendar-sync", state_class=DAY, value=lambda a: a.get("last_day_updates")),
)
SUM_SENSORS = (
    StatDescription(key="installs_total", translation_key="installs_total", icon="mdi:download", state_class=TOTAL, value=lambda a: a["installs"]),
    StatDescription(key="updates_total", translation_key="updates_total", icon="mdi:update", state_class=TOTAL, value=lambda a: a["updates"]),
)


def _apple(data: dict, app_id: str) -> dict | None:
    return ((data.get("apple") or {}).get("apps") or {}).get(app_id)


def _google(data: dict, pkg: str) -> dict | None:
    return ((data.get("google") or {}).get("apps") or {}).get(pkg)


def _sum(parts: list[dict | None], apple_flags: list[bool]) -> dict | None:
    present = [(p, is_apple) for p, is_apple in zip(parts, apple_flags) if p]
    if not present:
        return None
    return {
        "installs": sum((p.get("installs_effective", p["installs"]) if is_apple else p["installs"]) or 0 for p, is_apple in present),
        "updates": sum(p["updates"] or 0 for p, _ in present),
    }


class StatSensor(CoordinatorEntity[AppStatsCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: AppStatsCoordinator, description: StatDescription, uid: str, device: DeviceInfo, source: Callable[[dict], dict | None]) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{uid}_{description.key}"
        self._attr_device_info = device
        self._source = source

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None and self._source(self.coordinator.data) is not None

    @property
    def native_value(self) -> Any:
        src = self._source(self.coordinator.data or {})
        return None if src is None else self.entity_description.value(src)


class StatusSensor(CoordinatorEntity[AppStatsCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information-outline"

    def __init__(self, coordinator: AppStatsCoordinator, device: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_status"
        self._attr_device_info = device

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        if not self.coordinator.last_update_success:
            return "Fehler"
        if self.coordinator.data is None:
            return "Lädt Verlauf…"
        return "Fehler" if self.coordinator.data.get("errors") else "OK"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data or {}
        apple = d.get("apple") or {}
        return {
            "letzte_aktualisierung": d.get("updated"),
            "apple_letzter_berichtstag": apple.get("last_day"),
            "apple_aeltester_monat_mit_daten": apple.get("oldest_month_with_data"),
            "fehler": d.get("errors") or (str(self.coordinator.last_exception) if self.coordinator.last_exception else None),
        }


async def async_setup_entry(hass: HomeAssistant, entry: AppStatsConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    hub = DeviceInfo(identifiers={(DOMAIN, "all")}, name="Alle Apps", entry_type=DeviceEntryType.SERVICE, manufacturer="App-Statistiken")
    links: dict[str, str] = entry.options.get(OPT_LINKS, {})
    known: set[str] = set()

    def all_apps(data: dict) -> dict:
        apple = (data.get("apple") or {}).get("apps") or {}
        google = (data.get("google") or {}).get("apps") or {}
        return _sum([*apple.values(), *google.values()], [True] * len(apple) + [False] * len(google)) or {"installs": 0, "updates": 0}

    entities: list[SensorEntity] = [StatusSensor(coordinator, hub)]
    entities += [StatSensor(coordinator, d, f"{entry.entry_id}_all", hub, all_apps) for d in SUM_SENSORS]
    async_add_entities(entities)

    @callback
    def add_new() -> None:
        data = coordinator.data or {}
        new: list[SensorEntity] = []
        for app_id, a in ((data.get("apple") or {}).get("apps") or {}).items():
            uid = f"apple_{app_id}"
            if uid in known:
                continue
            known.add(uid)
            dev = DeviceInfo(identifiers={(DOMAIN, uid)}, name=f"{a['title']} (App Store)", entry_type=DeviceEntryType.SERVICE, manufacturer="Apple")
            new += [StatSensor(coordinator, d, uid, dev, lambda data, i=app_id: _apple(data, i)) for d in APPLE_SENSORS]
        for pkg in ((data.get("google") or {}).get("apps") or {}):
            uid = f"google_{pkg}"
            if uid in known:
                continue
            known.add(uid)
            dev = DeviceInfo(identifiers={(DOMAIN, uid)}, name=f"{pkg} (Google Play)", entry_type=DeviceEntryType.SERVICE, manufacturer="Google")
            new += [StatSensor(coordinator, d, uid, dev, lambda data, p=pkg: _google(data, p)) for d in GOOGLE_SENSORS]
        for pkg, app_id in links.items():
            uid = f"linked_{pkg}"
            a = _apple(data, app_id)
            if uid in known or a is None:
                continue
            known.add(uid)
            dev = DeviceInfo(identifiers={(DOMAIN, uid)}, name=f"{a['title']} (beide Stores)", entry_type=DeviceEntryType.SERVICE, manufacturer="App-Statistiken")
            new += [
                StatSensor(coordinator, d, uid, dev, lambda data, p=pkg, i=app_id: _sum([_apple(data, i), _google(data, p)], [True, False]))
                for d in SUM_SENSORS
            ]
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
