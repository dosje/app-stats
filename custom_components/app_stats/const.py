"""Constants for the App-Statistiken integration."""

from datetime import timedelta

DOMAIN = "app_stats"

CONF_APPLE_ISSUER_ID = "apple_issuer_id"
CONF_APPLE_KEY_ID = "apple_key_id"
CONF_APPLE_VENDOR = "apple_vendor_number"
CONF_APPLE_PRIVATE_KEY = "apple_private_key"
CONF_GOOGLE_BUCKET = "google_bucket"
CONF_GOOGLE_SERVICE_ACCOUNT = "google_service_account"

OPT_INCLUDE_REDOWNLOADS = "include_redownloads"
OPT_LINKS = "links"  # {google package name: apple app id}

UPDATE_INTERVAL = timedelta(hours=6)

# Apple: daily reports exist for the last 365 days; older history comes from monthly reports.
APPLE_MONTHS_BACK = 60
APPLE_CONCURRENCY = 4
APPLE_RECHECK_DAYS = 4  # empty daily reports this recent are fetched again (Apple publishes with a delay)

STORAGE_VERSION = 1
