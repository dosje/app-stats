"""Read-only clients for App Store Connect (Sales reports) and Google Play (Cloud Storage reports)."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import quote

import aiohttp
import jwt

from .parsers import google_bucket_name

APPLE_URL = "https://api.appstoreconnect.apple.com/v1/salesReports"
GCS_URL = "https://storage.googleapis.com/storage/v1/b"
GCS_SCOPE = "https://www.googleapis.com/auth/devstorage.read_only"


class StoreAuthError(Exception):
    """Credentials rejected."""


class StoreError(Exception):
    """Other API problem."""


class AppleClient:
    """App Store Connect API, Sales role is enough."""

    def __init__(self, session: aiohttp.ClientSession, issuer_id: str, key_id: str, vendor: str, private_key: str) -> None:
        self._session = session
        self._issuer = issuer_id.strip()
        self._key_id = key_id.strip()
        self._vendor = vendor.strip()
        self._key = private_key.strip()
        self._token: str | None = None
        self._token_exp = 0.0

    def _jwt(self) -> str:
        now = time.time()
        if self._token and now < self._token_exp - 60:
            return self._token
        self._token_exp = now + 1100  # Apple allows at most 20 minutes
        self._token = jwt.encode(
            {"iss": self._issuer, "iat": int(now), "exp": int(self._token_exp), "aud": "appstoreconnect-v1"},
            self._key,
            algorithm="ES256",
            headers={"kid": self._key_id, "typ": "JWT"},
        )
        return self._token

    async def sales_report(self, frequency: str, report_date: str) -> bytes | None:
        """Return the raw report, or None when Apple has no report for that period."""
        params = {
            "filter[frequency]": frequency,
            "filter[reportDate]": report_date,
            "filter[reportSubType]": "SUMMARY",
            "filter[reportType]": "SALES",
            "filter[vendorNumber]": self._vendor,
            "filter[version]": "1_0",
        }
        try:
            token = self._jwt()
        except Exception as err:  # invalid key file content
            raise StoreAuthError(f"Apple-Schlüssel ungültig: {err}") from err
        async with self._session.get(
            APPLE_URL, params=params, headers={"Authorization": f"Bearer {token}", "Accept": "application/a-gzip"}
        ) as resp:
            if resp.status == 200:
                return await resp.read()
            body = await resp.text()
            if resp.status in (401, 403):
                raise StoreAuthError(f"Apple {resp.status}: {body[:200]}")
            if resp.status == 404 or (resp.status == 400 and "INVALID" in body):
                return None  # no sales for that period, or period outside what Apple keeps
            raise StoreError(f"Apple {resp.status}: {body[:200]}")


class GoogleClient:
    """Google Cloud Storage with a service account that can only read the Play Console reports bucket."""

    def __init__(self, session: aiohttp.ClientSession, bucket: str, service_account_json: str) -> None:
        self._session = session
        self._bucket = google_bucket_name(bucket)
        try:
            self._sa: dict[str, Any] = json.loads(service_account_json)
        except ValueError as err:
            raise StoreAuthError("Service-Account-JSON ist ungültig") from err
        for key in ("client_email", "private_key"):
            if key not in self._sa:
                raise StoreAuthError(f"Service-Account-JSON ohne '{key}'")
        self._token: str | None = None
        self._token_exp = 0.0

    async def _access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_exp - 120:
            return self._token
        token_uri = self._sa.get("token_uri", "https://oauth2.googleapis.com/token")
        try:
            assertion = jwt.encode(
                {"iss": self._sa["client_email"], "scope": GCS_SCOPE, "aud": token_uri, "iat": int(now), "exp": int(now) + 3600},
                self._sa["private_key"],
                algorithm="RS256",
            )
        except Exception as err:
            raise StoreAuthError(f"Google-Schlüssel ungültig: {err}") from err
        async with self._session.post(
            token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or "access_token" not in data:
                raise StoreAuthError(f"Google-Anmeldung abgelehnt: {str(data)[:200]}")
        self._token = data["access_token"]
        self._token_exp = now + int(data.get("expires_in", 3600))
        return self._token

    async def _get(self, url: str, params: dict | None = None) -> aiohttp.ClientResponse:
        token = await self._access_token()
        resp = await self._session.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        if resp.status in (401, 403):
            body = await resp.text()
            resp.release()
            raise StoreAuthError(f"Google {resp.status}: {body[:200]}")
        if resp.status != 200:
            body = await resp.text()
            resp.release()
            raise StoreError(f"Google {resp.status}: {body[:200]}")
        return resp

    async def list_install_reports(self) -> list[dict]:
        """List stats/installs/*.csv objects: [{"name", "generation"}]."""
        items: list[dict] = []
        page = None
        while True:
            params = {"prefix": "stats/installs/", "fields": "items(name,generation),nextPageToken"}
            if page:
                params["pageToken"] = page
            resp = await self._get(f"{GCS_URL}/{self._bucket}/o", params)
            data = await resp.json()
            resp.release()
            items += data.get("items", [])
            page = data.get("nextPageToken")
            if not page:
                return items

    async def download(self, name: str) -> bytes:
        resp = await self._get(f"{GCS_URL}/{self._bucket}/o/{quote(name, safe='')}", {"alt": "media"})
        try:
            return await resp.read()
        finally:
            resp.release()
