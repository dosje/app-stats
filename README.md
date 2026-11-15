# App-Statistiken (App Store & Google Play)

<img src="custom_components/app_stats/brand/icon.png" width="96" alt="App-Statistiken icon">

Home Assistant integration that shows the **total installs and updates** of your own apps from
App Store Connect and Google Play, per app, per store, combined across both stores, and over all apps.

It only **reads** reports, with read-only keys:

- **Apple:** an App Store Connect API key with the role **Sales** (Sales and Trends reports).
- **Google:** a service account with only **"View app information and download bulk reports"**
  in the Play Console (reads the monthly install reports in the Play Console's Cloud Storage bucket).

Contents: [Installation](#installation) · [Setup](#setup) ([Apple](#apple-app-store-connect) · [Google](#google-play-console)) ·
[Options](#options) · [Sensors](#sensors) · [How the totals are built](#how-the-totals-are-built) ·
[Troubleshooting](#troubleshooting) · [Privacy](#privacy)

## Installation

1. HACS → ⋮ → **Custom repositories** → `https://github.com/dosje/app-stats`, type **Integration**.
2. Download **App-Statistiken**, then restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *App-Statistiken*.
4. Fill in Apple, Google or both (see [Setup](#setup)). Each field in the form also has a short help text.

The other store can be added later: open the integration → ⋮ → **Reconfigure**.

## Setup

You need four values from Apple and two from Google. Collect them first, then fill in the form.

| Form field | Where it comes from |
| --- | --- |
| Apple Issuer ID | App Store Connect API page, above the key list |
| Apple Key ID | App Store Connect API page, row of your key |
| Apple vendor number | Payments and Financial Reports, top left |
| Apple private key | Content of the downloaded `AuthKey_….p8` file |
| Google bucket | Play Console → Download reports → Statistics → *Copy Cloud Storage URI* |
| Google service account | Content of the service account's JSON key file |

### Apple (App Store Connect)

You need the **Account Holder** or **Admin** role to create the key.

1. Open [App Store Connect](https://appstoreconnect.apple.com) → **Users and Access** → **Integrations** →
   **App Store Connect API** → **Team Keys**. The first time, click **Request Access** and confirm.
2. Click **+** (Generate API Key):
   - Name: e.g. `Home Assistant`
   - Access: **Sales** only. This role can read reports but cannot change anything.
3. After **Generate**, note:
   - **Issuer ID**: shown above the key list (a long ID with dashes).
   - **Key ID**: 10 characters, in the row of the new key.
4. Click **Download API Key**. This gives `AuthKey_XXXXXXXXXX.p8`. **Apple allows this download only once**,
   so store the file safely (for example in a password manager).
5. **Vendor number:** App Store Connect → **Payments and Financial Reports**. The number at the top left,
   under the account name (8 digits, usually starting with 8). It is also shown in **Sales and Trends** → **Reports**.
6. In the Home Assistant form, paste the **full content** of the `.p8` file, including the
   `-----BEGIN PRIVATE KEY-----` and `-----END PRIVATE KEY-----` lines.
   On a Mac, `pbcopy < AuthKey_XXXXXXXXXX.p8` copies it exactly (rich-text editors can change characters).

### Google (Play Console)

This needs a Google Cloud service account that the Play Console allows to read its reports.

1. **Bucket:** [Play Console](https://play.google.com/console) → **Download reports** → **Statistics** →
   **Copy Cloud Storage URI**. It looks like `gs://pubsite_prod_…/stats/…`; the form only uses the bucket name.
2. **Service account:** in the [Google Cloud console](https://console.cloud.google.com), pick or create a project, then
   **IAM & Admin** → **Service accounts** → **Create service account** (name e.g. `home-assistant-reports`).
   No project roles are needed.
3. **Key:** open the service account → **Keys** → **Add key** → **Create new key** → **JSON**.
   A `.json` file is downloaded.
4. **Permission:** Play Console → **Users and permissions** → **Invite new users** → enter the service account's
   email (`…@….iam.gserviceaccount.com`). Under **Account permissions**, enable only
   **View app information and download bulk reports (read-only)**, then send the invitation.
5. In the Home Assistant form, enter the bucket and paste the **full content** of the JSON key file.

Google can take up to 24 hours before a newly invited service account can read the bucket. If the form says that
Google rejected the service account, try again later.

## Options

Open the integration → **Configure**:

- **Update interval (hours):** how often the stores are checked, 1–24, default 6. Both stores publish new numbers
  once a day.
- **Count App Store re-downloads as installs:** off by default, so each person counts once.
- **Link apps:** for each Google Play app, choose the same app in the App Store to get combined
  "(both stores)" totals.

## Sensors

| Device | Sensors |
| --- | --- |
| `<App> (App Store)` | Installs total, Updates total, Re-downloads total, Installs last day, Updates last day |
| `<package> (Google Play)` | Installs total, Updates total, Active installs, Installs last day, Updates last day |
| `<App> (both stores)` | Installs total, Updates total (after linking the app in the options) |
| `All apps` | Installs total, Updates total, Status (diagnostic) |

New apps appear automatically. Store data arrives with a delay of about one to four days (Apple) and one to
two days (Google).

## How the totals are built

- **Apple:** first-time downloads (product types 1, 1F, 1T, F1, …) and updates (7, 7F, 7T, F7). Daily reports are
  fetched for the last 365 days and **kept permanently**; for older months without daily data, monthly reports
  are used where Apple still provides them. Re-downloads (3, 3F, …) are counted separately. In-app purchases are
  ignored.
- **Google:** "Total User Installs" from the newest monthly installs overview report; updates are the sum of
  "Update events" (or "Daily Device Upgrades" in older reports) over all months.
- Fetched reports are cached in Home Assistant storage; after the first run only new days are fetched.

## Troubleshooting

The **Status** sensor of the "All apps" device shows `OK`, `Lädt Verlauf…` (first run) or `Fehler`, with details
in its attributes.

| Message | Cause |
| --- | --- |
| Apple rejected the key | Issuer ID and Key ID swapped, key without the Sales role, or incomplete `.p8` content |
| Apple: all four fields are needed | One of the Apple fields is empty |
| Google rejected the service account | Wrong JSON content, account not invited in the Play Console yet, or the permission is still being applied (up to 24 h) |
| Could not reach the store | Network problem; Home Assistant retries at the next interval |

## Privacy

- Only report numbers are read; nothing is written to the stores.
- The keys are stored in Home Assistant's configuration, like those of any other integration. Use read-only keys only.
- Cached report sums are stored in Home Assistant (`.storage/app_stats.<entry id>`). No data is sent anywhere else.

## Development

Tests use `pytest-homeassistant-custom-component` with simulated store APIs:

```bash
pip install pytest-homeassistant-custom-component
pytest
```
