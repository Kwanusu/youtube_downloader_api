"""Zoho -> YouTube -> Zindua LMS pipeline.

Each recording is matched to its cohort's class session in the LMS, uploaded
to that cohort's playlist, and its link is attached to the session so enrolled
students see it. Every step is recorded so a crash or retry never re-uploads a
video, and a recording is deleted from Zoho only once its video is confirmed
on YouTube and linked in the LMS.
"""
import shutil
import threading

import config
import notify
import routing
import store
import youtube_client
from lms_client import LmsClient
from youtube_client import QuotaExceeded
from zoho_client import ZohoClient, mentor_keys

_cycle_lock = threading.Lock()


def recorded_at(rec):
    start = routing.recording_start(rec)
    if start:
        return routing.local_date(start).isoformat()
    return str(rec.get("startTime") or rec.get("startDate") or "")[:10]


def video_title(rec, match):
    date = recorded_at(rec)
    topic = (rec.get("topic") or match.session_title or "Class Session").strip()
    title = f"{match.cohort_name} | {topic}"
    return f"{title} ({date})" if date else title


def video_description(rec, match):
    lines = [f"Cohort: {match.cohort_name}"]
    if match.session_title:
        lines.append(f"Class: {match.session_title}")
    host = rec.get("creatorName") or rec.get("hostName") or rec.get("creatorEmail")
    if host:
        lines.append(f"Mentor: {host}")
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


def _route(conn, row, rec, catalog):
    """The recording's cohort/session: an admin's assignment wins over automatic matching."""
    if row["match_reason"] == "manual" and row["cohort_id"]:
        cohort = store.get_cohort(conn, row["cohort_id"])
        if not cohort:
            raise RuntimeError(f"Cohort {row['cohort_id']} is not an active LMS cohort")
        title = next((s.title for s in catalog.sessions if s.session_id == row["session_id"]), "")
        return routing.Match(cohort["cohort_id"], cohort["name"], row["session_id"], title, "manual")
    return catalog.resolve(rec, mentor_keys(rec))


def _cohort_playlist(conn, youtube, match):
    cohort = store.get_cohort(conn, match.cohort_id)
    if cohort and cohort["playlist_id"]:
        return cohort["playlist_id"]
    print(f"--> Creating playlist for cohort '{match.cohort_name}'")
    playlist_id = youtube_client.create_playlist(
        youtube, f"{match.cohort_name} - Class Recordings",
        f"Recorded classes for the {match.cohort_name} cohort.",
    )
    store.set_cohort_playlist(conn, match.cohort_id, playlist_id)
    return playlist_id


def process_recording(conn, zoho, youtube, lms, catalog, rec):
    """Advance one recording as far as possible. Returns its final status."""
    rec_id = str(rec.get("erecordingId") or "").strip()
    download_url = rec.get("downloadUrl")
    if not rec_id or not download_url:
        return "skipped"  # still processing in Zoho, or malformed

    keys = mentor_keys(rec)
    row = store.ensure_recording(conn, rec_id, rec.get("topic"), keys[0] if keys else None, recorded_at(rec))
    if row["status"] in ("done", "failed"):
        return row["status"]

    try:
        match = _route(conn, row, rec, catalog)
    except Exception as e:
        store.record_failure(conn, rec_id, e)
        return "error"
    if not match:
        if row["status"] != "needs_review":
            store.update_recording(conn, rec_id, status="needs_review")
            notify.alert(
                "Recording needs a cohort",
                f"'{rec.get('topic')}' (host {keys or 'unknown'}, {recorded_at(rec)}) does not match "
                "any LMS class session. Assign it to a cohort on the dashboard; the recording "
                "is kept in Zoho until then.",
            )
        return "needs_review"
    if (row["cohort_id"], row["session_id"]) != (match.cohort_id, match.session_id) \
            or row["status"] == "needs_review":
        store.update_recording(conn, rec_id, cohort_id=match.cohort_id, session_id=match.session_id,
                               match_reason=match.reason,
                               status="uploaded" if row["video_id"] else "pending")

    title = video_title(rec, match)
    try:
        playlist_id = _cohort_playlist(conn, youtube, match)
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
                    youtube, str(path), title, video_description(rec, match), _mimetype(content_type)
                )
            finally:
                path.unlink(missing_ok=True)
            store.update_recording(conn, rec_id, status="uploaded", video_id=video_id)

        if row["playlist_id"] != playlist_id:
            print(f"--> Adding to playlist '{match.cohort_name}': {title}")
            youtube_client.add_to_playlist(youtube, video_id, playlist_id)
            store.update_recording(conn, rec_id, playlist_id=playlist_id)

        if not youtube_client.video_exists(youtube, video_id):
            raise RuntimeError(f"Video {video_id} not found on YouTube; Zoho copy kept")

        if match.session_id and not row["lms_recording_id"]:
            print(f"--> Linking to LMS session {match.session_id}: {title}")
            lms_id = lms.add_session_recording(
                match.session_id, f"https://youtu.be/{video_id}",
                f"{(rec.get('topic') or match.session_title or 'Class recording').strip()} ({recorded_at(rec)})",
            )
            store.update_recording(conn, rec_id, lms_recording_id=str(lms_id or "linked"))

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


def _pending(conn, recordings):
    for rec in recordings:
        row = store.get_recording(conn, str(rec.get("erecordingId") or ""))
        if rec.get("downloadUrl") and not (row and row["status"] in ("done", "failed")):
            yield rec


def load_catalog(conn, lms):
    """Fetch active cohorts and their recent sessions, and remember the cohorts."""
    catalog = routing.Catalog.from_lms(lms)
    for cohort_id, (name, cohort_type) in catalog.cohorts.items():
        store.upsert_cohort(conn, cohort_id, name, cohort_type)
    return catalog


def run_cycle(conn, zoho=None, youtube=None, lms=None):
    """Fetch Zoho recordings and process them. Safe to call concurrently (calls serialize)."""
    if not _cycle_lock.acquire(blocking=False):
        print("Cycle already running; skipping.")
        return {}
    try:
        zoho = zoho or ZohoClient()
        recordings = list(_pending(conn, zoho.list_recordings()))
        print(f"Found {len(recordings)} recording(s) to process in Zoho.")
        if not recordings:
            return {}
        try:
            youtube = youtube or youtube_client.get_service()
        except youtube_client.YouTubeAuthError as e:
            notify.alert("YouTube login needed", str(e))
            return {}
        lms = lms or LmsClient()
        try:
            catalog = load_catalog(conn, lms)
        except Exception as e:
            notify.alert("Could not read the LMS", f"Cohort schedules could not be loaded: {e}\n"
                         "Recordings are kept in Zoho and retried next cycle.")
            return {}
        results = {}
        for rec in recordings:
            try:
                status = process_recording(conn, zoho, youtube, lms, catalog, rec)
            except QuotaExceeded:
                print("YouTube daily quota reached; remaining recordings will be retried next cycle.")
                break
            results[status] = results.get(status, 0) + 1
        return results
    finally:
        _cycle_lock.release()
