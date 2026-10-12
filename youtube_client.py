"""YouTube Data API helpers: non-interactive auth, retrying uploads, playlists."""
import random
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

import config

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
UPLOAD_CHUNK_SIZE = 16 * 1024 * 1024
RETRIABLE_STATUS = {500, 502, 503, 504}
MAX_CHUNK_RETRIES = 8


class QuotaExceeded(RuntimeError):
    """Daily YouTube API quota used up; stop for today, not a recording failure."""


class YouTubeAuthError(RuntimeError):
    pass


def get_service():
    """Load token.json created by authorize_youtube.py. Never opens a browser."""
    if not config.YOUTUBE_TOKEN_FILE.exists():
        raise YouTubeAuthError(
            f"{config.YOUTUBE_TOKEN_FILE} not found. Run authorize_youtube.py on a "
            "machine with a browser and copy token.json to the server."
        )
    creds = Credentials.from_authorized_user_file(str(config.YOUTUBE_TOKEN_FILE), SCOPES)
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            raise YouTubeAuthError("YouTube token is invalid; re-run authorize_youtube.py.")
        try:
            creds.refresh(Request())
        except Exception as e:
            raise YouTubeAuthError(f"YouTube token refresh failed ({e}); re-run authorize_youtube.py.")
        config.YOUTUBE_TOKEN_FILE.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _is_quota_error(error):
    return error.resp.status == 403 and b"quotaExceeded" in (error.content or b"")


def upload_video(youtube, file_path, title, description, mimetype="video/mp4"):
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "categoryId": config.YOUTUBE_CATEGORY_ID,
        },
        "status": {"privacyStatus": config.YOUTUBE_PRIVACY, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(file_path, chunksize=UPLOAD_CHUNK_SIZE, resumable=True, mimetype=mimetype)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response, retries = None, 0
    while response is None:
        try:
            status, response = request.next_chunk()
            retries = 0
            if status:
                print(f"   upload {int(status.progress() * 100)}%: {title}")
        except HttpError as e:
            if _is_quota_error(e):
                raise QuotaExceeded("YouTube upload quota exceeded") from e
            if e.resp.status not in RETRIABLE_STATUS:
                raise
            retries = _backoff(retries, e)
        except (ConnectionError, TimeoutError, OSError) as e:
            retries = _backoff(retries, e)
    return response["id"]


def _backoff(retries, error):
    retries += 1
    if retries > MAX_CHUNK_RETRIES:
        raise RuntimeError(f"Upload failed after {MAX_CHUNK_RETRIES} retries: {error}")
    delay = min(2 ** retries, 120) + random.random()
    print(f"   transient upload error ({error}); retrying in {delay:.0f}s")
    time.sleep(delay)
    return retries


def create_playlist(youtube, title, description=""):
    """Create a playlist with the same privacy as uploaded videos. Returns its id."""
    try:
        return youtube.playlists().insert(
            part="snippet,status",
            body={"snippet": {"title": title[:150], "description": description[:5000]},
                  "status": {"privacyStatus": config.YOUTUBE_PRIVACY}},
        ).execute()["id"]
    except HttpError as e:
        if _is_quota_error(e):
            raise QuotaExceeded("YouTube quota exceeded") from e
        raise


def add_to_playlist(youtube, video_id, playlist_id):
    try:
        return youtube.playlistItems().insert(
            part="snippet",
            body={"snippet": {"playlistId": playlist_id,
                              "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
        ).execute()
    except HttpError as e:
        if _is_quota_error(e):
            raise QuotaExceeded("YouTube quota exceeded") from e
        raise


def video_exists(youtube, video_id):
    items = youtube.videos().list(part="status", id=video_id).execute().get("items", [])
    return bool(items) and items[0]["status"].get("uploadStatus") not in ("failed", "rejected", "deleted")
