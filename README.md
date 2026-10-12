# Class Recording Uploader (Zoho Meeting → YouTube → Zindua LMS)

When a technical mentor's Zoho Meeting recording is ready, this service:

1. finds the **cohort class session** it belongs to in the Zindua LMS,
2. downloads it and uploads it to YouTube (unlisted) titled `Cohort | Topic (date)`,
3. adds it to **that cohort's playlist** (one playlist per cohort, created automatically),
4. attaches the YouTube link to the **LMS class session**, so only students enrolled in that cohort see it,
5. confirms the video exists on YouTube, then **deletes the recording from Zoho**.

Recordings that cannot be matched to a cohort are **kept in Zoho** and flagged on the dashboard for an admin.

### How a recording finds its cohort

A technical mentor (TM) can run full-time and part-time cohorts at the same time, so the host alone is
not enough. All times are **Africa/Nairobi**. In order of confidence:

| # | Rule | Example |
|---|---|---|
| 1 | The Zoho meeting key matches the session's **meeting link**, and the recording starts during that session | `...videomeeting?key=1234567890` |
| 2 | The Zoho host e-mail is the session's **instructor**, and the recording starts during that session | TM's 19:00 part-time class |
| 3 | The host is the instructor of a cohort whose **slot** contains the recording, on that day | Full-time: weekdays 09:00–14:00. Part-time: 19:00–22:00 (covers 7–9 pm and 8–10 pm cohorts) |

"During" allows `MATCH_TOLERANCE_MINUTES` (45) either side of the session. Cohort type (`full-time` /
`part-time`) comes from the LMS cohort.

## Files

| File | Purpose |
|---|---|
| `server.py` | Web service: Zoho Flow webhook, dashboard, background worker |
| `pipeline.py` | Match → download → upload → playlist → verify → link in LMS → delete, with a resumable ledger |
| `routing.py` | Matches a recording to its cohort's class session (meeting link, instructor, time slot) |
| `lms_client.py` | Zindua LMS (Rukia API): cohorts, session schedules, session recordings |
| `zoho_client.py` | Zoho Meeting API (auto token refresh) |
| `youtube_client.py` | YouTube upload with retries and quota handling |
| `store.py` | SQLite: cohort→playlist map and per-recording status |
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

## 3. Zindua LMS

The service reads cohorts and class sessions and adds session recordings through the Rukia API,
as a **machine user** rather than a person:

1. In Zitadel (`https://auth.rukialabs.com`), in the same project as the LMS (so the token's audience
   matches), create a **service user** with *Access token type: JWT*, and generate a **client secret**.
2. Give that user an **admin** membership in the school's organisation in the LMS, so it can see every
   cohort's schedule and add recordings to any session.
3. In `.env` set `LMS_API_BASE_URL`, `LMS_ORG_ID` (the organisation UUID sent as `X-Org-Id`),
   `LMS_CLIENT_ID`, `LMS_CLIENT_SECRET` and
   `LMS_SCOPE=openid urn:zitadel:iam:org:project:id:<PROJECT_ID>:aud`.
   A personal access token in `LMS_ACCESS_TOKEN` works instead of client credentials.

TMs keep working as they do now: create the class for the cohort in the instructor app, choose
**Add meeting link**, and paste the Zoho Meeting link. Using the full Zoho link that contains the
meeting key gives the most reliable match.

Endpoints used: `GET /cohort/all?status=active`, `GET /cohort/{id}/schedule?scope=past`,
`GET /user/users/{id}`, `GET /event/session/{id}/recordings`, `POST /event/session-recording`.
Each session holds at most 5 recordings, which is an LMS limit.

## 4. Server (Ubuntu example)

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

## 5. Zoho Flow trigger (instant uploads)

In Zoho Flow: **Create flow → App trigger → Zoho Meeting → "Meeting recording uploaded"** (connect the admin account).
Action: **Webhook / Send HTTP request**, `POST https://uploads.yourschool.com/webhook/zoho?token=<WEBHOOK_SECRET>`.

The webhook just wakes the worker. The worker also checks Zoho every `POLL_INTERVAL_SECONDS` (15 min), so nothing is missed if Flow fails.

## 6. Dashboard

Open `https://uploads.yourschool.com/` and log in as **admin**. Active cohorts are listed automatically
from the LMS with their playlists. To reuse an existing playlist for a cohort, paste its URL before
that cohort's first upload. Mentors log in with `DASHBOARD_USER` / `DASHBOARD_PASSWORD` (view only).

If a recording shows *needs review*, no LMS session matched it (for example, an ad-hoc meeting, or a
class that was never scheduled in the LMS). Choose its cohort and, if possible, the LMS session id,
then click **Assign**. Without a session id, the video goes to the cohort playlist but is not linked
in the LMS.

## Tests

```bash
python -m unittest discover -p "test_*.py"
```
