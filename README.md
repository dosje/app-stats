# App-Statistiken (App Store & Google Play)

Home Assistant integration that shows the **total installs and updates** of your own apps from
App Store Connect and Google Play, per app, per store, combined across both stores, and over all apps.

It only **reads** reports, with read-only keys:

- **Apple:** App Store Connect API key with the role **Sales** (Sales and Trends reports).
- **Google:** a service account with only **"View app information and download bulk reports"**
  in the Play Console (reads the monthly install reports in the Play Console's Cloud Storage bucket).

## Sensors

| Device | Sensors |
| --- | --- |
| `<App> (App Store)` | Installs total, Updates total, Re-downloads total, Installs last day, Updates last day |
| `<package> (Google Play)` | Installs total, Updates total, Active installs, Installs last day, Updates last day |
| `<App> (both stores)` | Installs total, Updates total (after linking the app in the options) |
| `All apps` | Installs total, Updates total, Status (diagnostic) |

New apps appear automatically. Store data arrives with a delay of about one day (Apple) to two days (Google).

## How the totals are built

- **Apple:** first-time downloads (product types 1, 1F, 1T, F1, …) and updates (7, 7F, 7T, F7), summed from
  daily reports for the last 365 days and monthly reports up to 5 years before that. Re-downloads (3, 3F, …)
  are counted separately and can be added to installs in the options. In-app purchases are ignored.
- **Google:** "Total User Installs" from the newest monthly installs overview report; updates are the sum of
  "Update events" (or "Daily Device Upgrades" in older reports) over all months.
- Fetched reports are cached in Home Assistant storage; after the first run only the latest days are fetched,
  every 6 hours.

## Installation (HACS)

1. HACS → ⋮ → **Custom repositories** → `https://github.com/dosje/app-stats`, type **Integration**.
2. Download **App-Statistiken**, restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *App-Statistiken*.
4. Enter Apple, Google or both:
   - Apple: Issuer ID, Key ID, vendor number, and the content of the `.p8` key file.
   - Google: the bucket (`gs://pubsite_prod_…`, Play Console → Download reports → Statistics → *Copy Cloud Storage URI*)
     and the content of the service account's JSON key file.
5. Optional: **Configure** to link the same app on both stores and to count re-downloads.

The keys are stored in Home Assistant's configuration like those of any other integration, so use read-only keys only.

## Development

Tests use `pytest-homeassistant-custom-component` with simulated store APIs:

```bash
pip install pytest-homeassistant-custom-component
pytest
```
