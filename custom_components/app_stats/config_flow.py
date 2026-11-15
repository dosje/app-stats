"""Setup and options for App-Statistiken."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.util import dt as dt_util

from .api import AppleClient, GoogleClient, StoreAuthError, StoreError
from .const import (
    CONF_APPLE_ISSUER_ID,
    CONF_APPLE_KEY_ID,
    CONF_APPLE_PRIVATE_KEY,
    CONF_APPLE_VENDOR,
    CONF_GOOGLE_BUCKET,
    CONF_GOOGLE_SERVICE_ACCOUNT,
    DOMAIN,
    DEFAULT_SCAN_HOURS,
    OPT_INCLUDE_REDOWNLOADS,
    OPT_LINKS,
    OPT_SCAN_HOURS,
)

APPLE_KEYS = (CONF_APPLE_ISSUER_ID, CONF_APPLE_KEY_ID, CONF_APPLE_VENDOR, CONF_APPLE_PRIVATE_KEY)
GOOGLE_KEYS = (CONF_GOOGLE_BUCKET, CONF_GOOGLE_SERVICE_ACCOUNT)
MULTILINE = TextSelector(TextSelectorConfig(multiline=True))


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_APPLE_ISSUER_ID, default=defaults.get(CONF_APPLE_ISSUER_ID, "")): str,
            vol.Optional(CONF_APPLE_KEY_ID, default=defaults.get(CONF_APPLE_KEY_ID, "")): str,
            vol.Optional(CONF_APPLE_VENDOR, default=defaults.get(CONF_APPLE_VENDOR, "")): str,
            vol.Optional(CONF_APPLE_PRIVATE_KEY, default=""): MULTILINE,
            vol.Optional(CONF_GOOGLE_BUCKET, default=defaults.get(CONF_GOOGLE_BUCKET, "")): str,
            vol.Optional(CONF_GOOGLE_SERVICE_ACCOUNT, default=""): MULTILINE,
        }
    )


async def _validate(hass, data: dict[str, Any]) -> dict[str, str]:
    """Check the credentials with one read call per store. Returns form errors."""
    errors: dict[str, str] = {}
    apple_given = [k for k in APPLE_KEYS if data.get(k, "").strip()]
    google_given = [k for k in GOOGLE_KEYS if data.get(k, "").strip()]
    if not apple_given and not google_given:
        return {"base": "nothing_entered"}
    session = async_get_clientsession(hass)
    if apple_given:
        if len(apple_given) < len(APPLE_KEYS):
            errors["base"] = "apple_incomplete"
        else:
            try:
                client = AppleClient(session, data[CONF_APPLE_ISSUER_ID], data[CONF_APPLE_KEY_ID], data[CONF_APPLE_VENDOR], data[CONF_APPLE_PRIVATE_KEY])
                day = (dt_util.now().date() - timedelta(days=4)).isoformat()
                await client.sales_report("DAILY", day)
            except StoreAuthError:
                errors[CONF_APPLE_PRIVATE_KEY] = "apple_auth"
            except (StoreError, OSError):
                errors["base"] = "cannot_connect"
    if google_given:
        if len(google_given) < len(GOOGLE_KEYS):
            errors["base"] = "google_incomplete"
        else:
            try:
                client = GoogleClient(session, data[CONF_GOOGLE_BUCKET], data[CONF_GOOGLE_SERVICE_ACCOUNT])
                await client.list_install_reports()
            except StoreAuthError:
                errors[CONF_GOOGLE_SERVICE_ACCOUNT] = "google_auth"
            except (StoreError, OSError):
                errors["base"] = "cannot_connect"
    return errors


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v.strip() for k, v in data.items() if isinstance(v, str) and v.strip()}


class AppStatsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Enter the read-only store keys."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title="App-Statistiken", data=_clean(user_input))
        return self.async_show_form(step_id="user", data_schema=_schema(user_input or {}), errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reconfigure()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry() if self.source == "reconfigure" else self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = {**entry.data, **_clean(user_input)}
            errors = await _validate(self.hass, merged)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=merged)
        return self.async_show_form(step_id="reconfigure", data_schema=_schema(dict(entry.data)), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AppStatsOptionsFlow()


class AppStatsOptionsFlow(OptionsFlow):
    """Link the same app on both stores, and choose whether Apple re-downloads count as installs."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        coordinator = self.config_entry.runtime_data
        data = coordinator.data or {}
        apple_apps = ((data.get("apple") or {}).get("apps")) or {}
        google_apps = ((data.get("google") or {}).get("apps")) or {}
        links: dict[str, str] = dict(self.config_entry.options.get(OPT_LINKS, {}))

        if user_input is not None:
            new_links = {pkg: user_input[f"link::{pkg}"] for pkg in google_apps if user_input.get(f"link::{pkg}", "none") != "none"}
            return self.async_create_entry(
                data={
                    OPT_SCAN_HOURS: int(user_input[OPT_SCAN_HOURS]),
                    OPT_INCLUDE_REDOWNLOADS: user_input[OPT_INCLUDE_REDOWNLOADS],
                    OPT_LINKS: new_links,
                }
            )

        choices = [SelectOptionDict(value="none", label="– nicht verknüpft –")] + [
            SelectOptionDict(value=app_id, label=f"{a['title']} (App Store)") for app_id, a in apple_apps.items()
        ]
        schema: dict[Any, Any] = {
            vol.Required(OPT_SCAN_HOURS, default=int(self.config_entry.options.get(OPT_SCAN_HOURS, DEFAULT_SCAN_HOURS))): NumberSelector(
                NumberSelectorConfig(min=1, max=24, step=1, unit_of_measurement="h", mode=NumberSelectorMode.SLIDER)
            ),
            vol.Required(OPT_INCLUDE_REDOWNLOADS, default=self.config_entry.options.get(OPT_INCLUDE_REDOWNLOADS, False)): BooleanSelector()
        }
        for pkg in google_apps:
            schema[vol.Required(f"link::{pkg}", default=links.get(pkg, "none"))] = SelectSelector(
                SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN)
            )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            description_placeholders={"google_count": str(len(google_apps)), "apple_count": str(len(apple_apps))},
        )
