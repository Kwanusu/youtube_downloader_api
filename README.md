# Class Recording Uploader (Zoho Meeting → YouTube)

When a mentor's Zoho Meeting recording is ready, this service:

1. downloads it,
2. uploads it to YouTube (unlisted) titled `Class | Topic (date)`,
3. adds it to **that mentor's class playlist**,
4. confirms the video exists on YouTube, then **deletes the recording from Zoho**.

Mentors open the dashboard to see their uploads and links. Admins map each mentor to a class playlist there.
Recordings from mentors with no playlist set are **kept in Zoho** and flagged on the dashboard.

## Files

| File | Purpose |
|---|---|
| `server.py` | Web service: Zoho Flow webhook, dashboard, background worker |
| `pipeline.py` | Download → upload → playlist → verify → delete, with a resumable ledger |
| `zoho_client.py` | Zoho Meeting API (auto token refresh) |
| `youtube_client.py` | YouTube upload with retries and quota handling |
| `store.py` | SQLite: mentor→playlist map and per-recording status |
| `authorize_youtube.py` | One-time YouTube login (run on your PC, not the server) |
| `cronjob.py` | Run one cycle manually |
| `check_zoho.py` | Finds your Zoho data centre and org ID (zsoid) |

## 1. Zoho (admin account)

1. In <https://api-console.zoho.com> create a **Self Client**, signed in as the Zoho Meeting **admin**.
2. Generate a code with these scopes, then exchange it for a refresh token:
   `ZohoMeeting.recording.READ,ZohoMeeting.recording.DELETE,ZohoMeeting.manageOrg.READ`
   If downloads return `INVALID_OAUTHSCOPE`, add `ZohoFiles.files.READ` and regenerate.
3. Put the values in `.env` (copy from `.env.example`), then run `python check_zoho.py` to get `ZOHO_DOMAIN` and `ZOHO_ZSOID`.

## 2. YouTube

1. In Google Cloud Console enable **YouTube Data API v3** and create a *Desktop* OAuth client. Put its ID and secret in `.env`.
2. Set the OAuth consent screen to **In production**. In *Testing* mode, Google logins expire every 7 days.
3. On your own PC, signed in as the channel owner, run:
   `python authorize_youtube.py`. Copy the resulting `token.json` to the server.
4. **Quota:** the default 10,000 units/day allows about **6 uploads per day** in total. Request more quota
   (YouTube API Services audit form) based on how many classes you record per day.
   Until Google audits the project, uploads may be forced to **private**.
   When the quota runs out, the service stops and continues the next day. Nothing is lost.

## 3. Server (Ubuntu example)

```bash
sudo useradd -r -m -d /opt/class-uploader uploader
sudo -u uploader git clone <this repo> /opt/class-uploader   # or copy the files
cd /opt/class-uploader
sudo -u uploader python3 -m venv venv
sudo -u uploader venv/bin/pip install -r requirements.txt
# put .env and token.json in /opt/class-uploader
sudo cp deploy/class-uploader.service /etc/systemd/system/
sudo systemctl enable --now class-uploader
```

Put HTTPS in front of it (Caddy: `uploads.yourschool.com { reverse_proxy 127.0.0.1:8080 }`).
Make sure the disk has room for your largest recording. Files are deleted right after upload.

## 4. Zoho Flow trigger (instant uploads)

In Zoho Flow: **Create flow → App trigger → Zoho Meeting → "Meeting recording uploaded"** (connect the admin account).
Action: **Webhook / Send HTTP request**, `POST https://uploads.yourschool.com/webhook/zoho?token=<WEBHOOK_SECRET>`.

The webhook just wakes the worker. The worker also checks Zoho every `POLL_INTERVAL_SECONDS` (15 min), so nothing is missed if Flow fails.

## 5. Add mentors

Open `https://uploads.yourschool.com/` and log in as **admin**. For each mentor enter:
their **Zoho host email**, name, class name, and the YouTube **playlist URL**.
Mentors log in with the `DASHBOARD_USER` / `DASHBOARD_PASSWORD` (view only).

If a recording appears as *needs mapping*, its host email is shown in the table. Add that email as a mentor
and the recording is processed automatically.

## Tests

```bash
python -m unittest test_cronjob
```
