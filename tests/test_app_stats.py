"""Tests for app_stats with simulated App Store Connect and Google Cloud Storage."""

from __future__ import annotations

import gzip
import json
from datetime import date, timedelta
from urllib.parse import parse_qs

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.app_stats.const import DOMAIN, OPT_INCLUDE_REDOWNLOADS, OPT_LINKS, OPT_SCAN_HOURS
from custom_components.app_stats.coordinator import apple_plan, month_covered_by_daily
from custom_components.app_stats.parsers import parse_apple_sales_report, parse_google_overview

APPLE_URL = "https://api.appstoreconnect.apple.com/v1/salesReports"
BUCKET = "pubsite_prod_rev_123"
GCS_LIST = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o"
TOKEN_URI = "https://oauth2.googleapis.com/token"
APP_ID = "1234567890"
PKG = "com.example.my_app"

EC_KEY = ec.generate_private_key(ec.SECP256R1())
EC_PEM = EC_KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
RSA_PEM = RSA_KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
SA_JSON = json.dumps({"type": "service_account", "client_email": "ha@proj.iam.gserviceaccount.com", "private_key": RSA_PEM, "token_uri": TOKEN_URI})

HEADER = "Provider\tProvider Country\tSKU\tDeveloper\tTitle\tVersion\tProduct Type Identifier\tUnits\tDeveloper Proceeds\tBegin Date\tEnd Date\tCustomer Currency\tCountry Code\tCurrency of Proceeds\tApple Identifier\tCustomer Price\tPromo Code\tParent Identifier\tSubscription\tPeriod\tCategory\tCMB\tDevice\tSupported Platforms\tProceeds Reason\tPreserved Pricing\tClient\tOrder Type"


def apple_row(ptype: str, units: int, app_id: str = APP_ID, title: str = "My App") -> str:
    cols = ["APPLE", "US", "sku1", "Dev", title, "1.0", ptype, str(units), "0", "11/01/2026", "11/01/2026", "EUR", "LU", "EUR", app_id, "0", "", "", "", "", "Utilities", "", "iPhone", "iOS", "", "", "", ""]
    return "\t".join(cols)


def apple_report(rows: list[str]) -> bytes:
    return gzip.compress(("\n".join([HEADER, *rows]) + "\n").encode())


def google_csv(rows: list[tuple]) -> bytes:
    head = "Date,Package Name,Daily Device Installs,Daily Device Uninstalls,Daily Device Upgrades,Total User Installs,Daily User Installs,Daily User Uninstalls,Active Device Installs,Install events,Update events,Uninstall events"
    lines = [head] + [f"{d},{PKG},{i},0,{u},{tot},{i},0,{act},{i},{u},0" for d, i, u, tot, act in rows]
    return ("\n".join(lines) + "\n").encode("utf-16")


# ---------------- parsers ----------------
def test_parse_apple():
    raw = apple_report([apple_row("1F", 5), apple_row("7F", 12), apple_row("3F", 2), apple_row("IA1", 99), apple_row("1F", -1), apple_row("1F", 3, "999", "Other")])
    apps = parse_apple_sales_report(raw)
    assert apps[APP_ID] == {"title": "My App", "installs": 4, "updates": 12, "redownloads": 2}
    assert apps["999"]["installs"] == 3


def test_parse_google_utf16():
    d = parse_google_overview(google_csv([("2026-10-01", 3, 10, 100, 80), ("2026-10-02", 4, 11, 104, 82)]))
    assert d["installs"] == 7 and d["updates"] == 21
    assert d["total_user_installs"] == 104 and d["active_installs"] == 82
    assert d["last_date"] == "2026-10-02" and d["last_day_installs"] == 4 and d["last_day_updates"] == 11


def test_plan_covers_each_day_once():
    today = date(2026, 11, 15)
    days, months = apple_plan(today)
    assert days[0] == date(2025, 12, 1) and days[-1] == date(2026, 11, 14)
    assert months[0] == date(2025, 11, 1) and len(months) == 60
    assert (today - days[0]).days <= 365


# ---------------- simulated stores ----------------
class FakeStores:
    def __init__(self):
        self.apple_calls = 0
        self.auth_ok = True
        today = dt_util.now().date()
        self.y = today - timedelta(days=1)
        _, months = apple_plan(today)
        self.month_old = months[2].strftime("%Y-%m")

    async def apple(self, method, url, data):
        self.apple_calls += 1
        q = {k: v[0] for k, v in parse_qs(url.query_string).items()}
        # check the JWT is a valid ES256 token for App Store Connect
        assert q["filter[reportType]"] == "SALES" and q["filter[vendorNumber]"] == "85000000"
        if not self.auth_ok:
            return AiohttpClientMockResponse(method, url, status=401, response=b'{"errors":[{"code":"NOT_AUTHORIZED"}]}')
        when = q["filter[reportDate]"]
        if q["filter[frequency]"] == "DAILY" and when == self.y.isoformat():
            return AiohttpClientMockResponse(method, url, status=200, response=apple_report([apple_row("1F", 7), apple_row("7F", 30)]))
        if q["filter[frequency]"] == "DAILY" and when == (self.y - timedelta(days=10)).isoformat():
            return AiohttpClientMockResponse(method, url, status=200, response=apple_report([apple_row("1F", 3), apple_row("3F", 2)]))
        if q["filter[frequency]"] == "MONTHLY" and when == self.month_old:
            return AiohttpClientMockResponse(method, url, status=200, response=apple_report([apple_row("1F", 100), apple_row("7F", 400)]))
        if q["filter[frequency]"] == "MONTHLY" and when < self.month_old:
            return AiohttpClientMockResponse(method, url, status=410, response=b'{"errors":[{"status":"410","code":"GONE_ERROR"}]}')
        return AiohttpClientMockResponse(method, url, status=404, response='{"errors":[{"code":"NOT_FOUND"}]}'.encode())


def mock_all(aioclient_mock, fake: FakeStores):
    aioclient_mock.get(APPLE_URL, side_effect=fake.apple)
    aioclient_mock.post(TOKEN_URI, json={"access_token": "ya29.test", "expires_in": 3600})
    files = {
        f"stats/installs/installs_{PKG}_202610_overview.csv": google_csv([("2026-10-01", 3, 10, 100, 80), ("2026-10-31", 4, 11, 107, 82)]),
        f"stats/installs/installs_{PKG}_202611_overview.csv": google_csv([("2026-11-01", 5, 6, 112, 85), ("2026-11-13", 2, 1, 114, 86)]),
        f"stats/installs/installs_{PKG}_202611_country.csv": b"ignored",
    }
    aioclient_mock.get(GCS_LIST, json={"items": [{"name": n, "generation": "1"} for n in files]})
    for name, body in files.items():
        from urllib.parse import quote

        aioclient_mock.get(f"{GCS_LIST}/{quote(name, safe='')}", content=body)


ENTRY_DATA = {
    "apple_issuer_id": "69a6de70-0000-47e3-e053-5b8c7c11a4d1",
    "apple_key_id": "ABC123DEFG",
    "apple_vendor_number": "85000000",
    "apple_private_key": EC_PEM,
    "google_bucket": f"gs://{BUCKET}/stats/installs",
    "google_service_account": SA_JSON,
}


@pytest.fixture(autouse=True)
def german(hass):
    hass.config.language = "de"


async def test_config_flow_validates_and_creates(hass: HomeAssistant, aioclient_mock):
    fake = FakeStores()
    mock_all(aioclient_mock, fake)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    # nothing entered
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "nothing_entered"}
    # apple incomplete
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"apple_issuer_id": "x"})
    assert result["errors"] == {"base": "apple_incomplete"}
    # bad apple key text
    bad = {**ENTRY_DATA, "apple_private_key": "not a key"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], bad)
    assert result["errors"].get("apple_private_key") == "apple_auth"
    # good
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["data"]["apple_vendor_number"] == "85000000"
    await hass.async_block_till_done()


async def test_rejected_by_apple(hass: HomeAssistant, aioclient_mock):
    fake = FakeStores()
    fake.auth_ok = False
    mock_all(aioclient_mock, fake)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["errors"].get("apple_private_key") == "apple_auth"


async def setup_entry(hass, aioclient_mock, options=None):
    fake = FakeStores()
    mock_all(aioclient_mock, fake)
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options=options or {}, title="App-Statistiken")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry, fake


def val(hass, entity_id):
    s = hass.states.get(entity_id)
    assert s is not None, f"missing {entity_id}: {sorted(hass.states.async_entity_ids('sensor'))}"
    return s.state


async def test_totals(hass: HomeAssistant, aioclient_mock):
    entry, fake = await setup_entry(hass, aioclient_mock)
    from datetime import timedelta as _td
    assert entry.runtime_data.update_interval == _td(hours=6)  # default
    ids = sorted(hass.states.async_entity_ids("sensor"))
    print("\n".join(ids))
    # Apple: daily 7 + 3 + monthly 100 = 110 installs, updates 30 + 400 = 430, redownloads 2
    assert val(hass, "sensor.my_app_app_store_installationen_gesamt") == "110"
    assert val(hass, "sensor.my_app_app_store_updates_gesamt") == "430"
    assert val(hass, "sensor.my_app_app_store_re_downloads_gesamt") == "2"
    assert val(hass, "sensor.my_app_app_store_installationen_letzter_tag") == "7"
    # Google: Total User Installs from newest month = 114; updates 10+11+6+1 = 28; active 86
    assert val(hass, "sensor.com_example_my_app_google_play_installationen_gesamt") == "114"
    assert val(hass, "sensor.com_example_my_app_google_play_updates_gesamt") == "28"
    assert val(hass, "sensor.com_example_my_app_google_play_aktive_installationen") == "86"
    # all apps
    assert val(hass, "sensor.alle_apps_installationen_gesamt") == "224"
    assert val(hass, "sensor.alle_apps_updates_gesamt") == "458"
    assert val(hass, "sensor.alle_apps_status") == "OK"
    calls_first = fake.apple_calls
    assert calls_first > 300  # full history fetched once

    # second refresh: only the recent days are fetched again (cache works)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert fake.apple_calls - calls_first <= 6, fake.apple_calls - calls_first
    assert val(hass, "sensor.alle_apps_installationen_gesamt") == "224"


async def test_options_link_and_redownloads(hass: HomeAssistant, aioclient_mock):
    entry, _ = await setup_entry(hass, aioclient_mock)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    keys = [str(k) for k in result["data_schema"].schema]
    assert f"link::{PKG}" in keys, keys
    result = await hass.config_entries.options.async_configure(result["flow_id"], {OPT_SCAN_HOURS: 12, OPT_INCLUDE_REDOWNLOADS: True, f"link::{PKG}": APP_ID})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_LINKS] == {PKG: APP_ID}
    assert entry.options[OPT_SCAN_HOURS] == 12
    await hass.async_block_till_done(wait_background_tasks=True)
    # combined device: apple 110 + 2 redownloads = 112, google 114 -> 226; updates 430 + 28
    assert val(hass, "sensor.my_app_beide_stores_installationen_gesamt") == "226"
    assert val(hass, "sensor.my_app_beide_stores_updates_gesamt") == "458"
    assert val(hass, "sensor.my_app_app_store_installationen_gesamt") == "112"
    from datetime import timedelta as _td
    assert entry.runtime_data.update_interval == _td(hours=12)


async def test_jwt_is_valid_for_apple():
    from custom_components.app_stats.api import AppleClient

    client = AppleClient(None, "iss-1", "KEY1", "850", EC_PEM)
    token = client._jwt()
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256" and header["kid"] == "KEY1"
    claims = jwt.decode(token, EC_KEY.public_key(), algorithms=["ES256"], audience="appstoreconnect-v1")
    assert claims["iss"] == "iss-1" and claims["exp"] - claims["iat"] <= 1200


def test_month_covered_by_daily():
    ac = {f"D:2025-02-{d:02d}": {"empty": True} for d in range(1, 29)}
    assert month_covered_by_daily(ac, date(2025, 2, 1))
    del ac["D:2025-02-14"]
    assert not month_covered_by_daily(ac, date(2025, 2, 1))


async def test_gone_months_are_not_retried(hass: HomeAssistant, aioclient_mock, caplog):
    entry, fake = await setup_entry(hass, aioclient_mock)
    assert "reports failed" not in caplog.text  # 410 'gone' is not an error
    first = fake.apple_calls
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert fake.apple_calls - first <= 6  # gone months are cached, not asked again


async def test_daily_data_kept_when_window_moves(hass: HomeAssistant, aioclient_mock):
    """Daily numbers older than Apple's 365-day window stay in the cache and are counted."""
    entry, fake = await setup_entry(hass, aioclient_mock)
    coord = entry.runtime_data
    _, months = apple_plan(dt_util.now().date())
    old = months[0]  # month just before the daily window
    d = old
    while d.month == old.month:  # pretend we fetched this month day by day a year ago
        coord._cache["apple"][f"D:{d.isoformat()}"] = {"empty": True}
        d += timedelta(days=1)
    coord._cache["apple"][f"D:{old.isoformat()}"] = {"apps": {APP_ID: {"title": "My App", "installs": 50, "updates": 5, "redownloads": 0}}}
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert val(hass, "sensor.my_app_app_store_installationen_gesamt") == "160"  # 110 + 50 kept from daily data
    assert f"M:{old.strftime('%Y-%m')}" not in coord._cache["apple"]
