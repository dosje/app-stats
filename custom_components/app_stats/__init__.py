"""App-Statistiken: totals of installs and updates from App Store Connect and Google Play (read-only)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import AppStatsCoordinator

PLATFORMS = [Platform.SENSOR]

type AppStatsConfigEntry = ConfigEntry[AppStatsCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AppStatsConfigEntry) -> bool:
    coordinator = AppStatsCoordinator(hass, entry)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # the first run can take a few minutes (full history), so it runs in the background
    entry.async_create_background_task(hass, coordinator.async_refresh(), "app_stats_first_refresh")
    entry.async_on_unload(entry.add_update_listener(_reload_on_options))
    return True


async def _reload_on_options(hass: HomeAssistant, entry: AppStatsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AppStatsConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
