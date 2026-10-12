"""Zoho -> YouTube pipeline. Every step is recorded so a crash or retry never
re-uploads a video, and a recording is deleted from Zoho only once its video
is confirmed on YouTube and in the mentor's class playlist."""
import shutil
import threading
from datetime import datetime, timezone

import config
import notify
import store
import youtube_client
from youtube_client import QuotaExceeded
from zoho_client import ZohoClient, mentor_keys

_cycle_lock = threading.Lock()


def recorded_at(rec):
    ms = rec.get("startTimeInMs") or rec.get("startTimeMillis")
    if ms:
        try:
            return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass
    return str(rec.get("startTime") or rec.get("startDate") or "")[:10]


def video_title(rec, mentor):
    date = recorded_at(rec)
    topic = (rec.get("topic") or "Class Session").strip()
    title = f"{mentor['class_name']} | {topic}"
    return f"{title} ({date})" if date else title


def video_description(rec, mentor):
    lines = [f"Class: {mentor['class_name']}", f"Mentor: {mentor['display_name']}"]
    if recorded_at(rec):
        lines.append(f"Recorded: {recorded_at(rec)}")
    if rec.get("duration"):
        lines.append(f"Duration: {rec['duration']}")
    return "\n".join(lines)


def _mimetype(content_type):
    content_type = (content_type or "").split(";")[0].strip()
    return content_type if content_type.startswith("video/") else "video/mp4"


def _check_disk_space(rec):
    size = rec.get("fileSize") or rec.get("size")
    try:
        needed = int(size) * 1.1
    except (TypeError, ValueError):
        return
    free = shutil.disk_usage(config.DOWNLOAD_DIR).free
    if free < needed:
        raise RuntimeError(f"Not enough disk space: need {needed / 1e9:.1f} GB, have {free / 1e9:.1f} GB")


def process_recording(conn, zoho, youtube, rec):
    """Advance one recording as far as possible. Returns its final status."""
    rec_id = str(rec.get("erecordingId") or "").strip()
    download_url = rec.get("downloadUrl")
    if not rec_id or not download_url:
        return "skipped"  # still processing in Zoho, or malformed

    keys = mentor_keys(rec)
    mentor = store.find_mentor(conn, keys)
    row = store.ensure_recording(
        conn, rec_id, rec.get("topic"), mentor["mentor_key"] if mentor else (keys[0] if keys else None),
        recorded_at(rec),
    )
    if row["status"] in ("done", "failed"):
        return row["status"]
    if not mentor:
        if row["status"] != "needs_mapping":
            store.update_recording(conn, rec_id, status="needs_mapping")
            notify.alert(
                "Recording has no class playlist",
                f"'{rec.get('topic')}' was hosted by {keys or 'an unknown host'}, who has no "
                "class playlist configured. Add the mentor on the dashboard; the recording "
                "is kept in Zoho until then.",
            )
        return "needs_mapping"
    if row["mentor_key"] != mentor["mentor_key"] or row["status"] == "needs_mapping":
        store.update_recording(conn, rec_id, mentor_key=mentor["mentor_key"],
                               status="uploaded" if row["video_id"] else "pending")

    title = video_title(rec, mentor)
    try:
        video_id = row["video_id"]
        if not video_id:
            config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            _check_disk_space(rec)
            path = config.DOWNLOAD_DIR / f"{rec_id}.mp4"
            try:
                print(f"--> Downloading: {title}")
                content_type = zoho.download_recording(download_url, path)
                print(f"--> Uploading: {title}")
                video_id = youtube_client.upload_video(
                    youtube, str(path), title, video_description(rec, mentor), _mimetype(content_type)
                )
            finally:
                path.unlink(missing_ok=True)
            store.update_recording(conn, rec_id, status="uploaded", video_id=video_id)

        if row["playlist_id"] != mentor["playlist_id"]:
            print(f"--> Adding to playlist '{mentor['class_name']}': {title}")
            youtube_client.add_to_playlist(youtube, video_id, mentor["playlist_id"])
            store.update_recording(conn, rec_id, playlist_id=mentor["playlist_id"])

        if not youtube_client.video_exists(youtube, video_id):
            raise RuntimeError(f"Video {video_id} not found on YouTube; Zoho copy kept")

        print(f"--> Deleting from Zoho: {title}")
        if not zoho.delete_recording(rec_id):
            raise RuntimeError("Zoho did not confirm deletion")
        store.update_recording(conn, rec_id, status="done", last_error=None)
        print(f"    DONE: https://youtu.be/{video_id}")
        return "done"

    except QuotaExceeded:
        raise
    except Exception as e:
        print(f"    ERROR on '{title}': {e}")
        store.record_failure(conn, rec_id, e)
        if store.get_recording(conn, rec_id)["status"] == "failed":
            notify.alert(f"Upload failed: {title}",
                         f"Gave up after {config.MAX_ATTEMPTS} attempts.\nLast error: {e}\n"
                         "Use the Retry button on the dashboard once the cause is fixed.")
        return "error"


def run_cycle(conn, zoho=None, youtube=None):
    """Fetch Zoho recordings and process them. Safe to call concurrently (calls serialize)."""
    if not _cycle_lock.acquire(blocking=False):
        print("Cycle already running; skipping.")
        return {}
    try:
        zoho = zoho or ZohoClient()
        try:
            youtube = youtube or youtube_client.get_service()
        except youtube_client.YouTubeAuthError as e:
            notify.alert("YouTube login needed", str(e))
            return {}
        recordings = zoho.list_recordings()
        print(f"Found {len(recordings)} recording(s) in Zoho.")
        results = {}
        for rec in recordings:
            try:
                status = process_recording(conn, zoho, youtube, rec)
            except QuotaExceeded:
                print("YouTube daily quota reached; remaining recordings will be retried next cycle.")
                break
            results[status] = results.get(status, 0) + 1
        return results
    finally:
        _cycle_lock.release()
