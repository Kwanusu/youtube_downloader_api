import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import check_zoho
import config
import pipeline
import routing
import server
import store
from youtube_client import QuotaExceeded
from zoho_client import ZohoClient, mentor_keys

# Monday 13 Oct 2025, 19:05 Africa/Nairobi (16:05 UTC): a part-time class.
PT_START = datetime(2025, 10, 13, 16, 0, tzinfo=timezone.utc)
FT_START = datetime(2025, 10, 13, 6, 0, tzinfo=timezone.utc)


def ms(dt):
    return int(dt.timestamp() * 1000)


REC = {
    "erecordingId": "rec-1",
    "downloadUrl": "https://example.test/video",
    "topic": "React hooks",
    "creatorEmail": "Jane@School.com",
    "meetingKey": "2222222222",
    "startTimeInMs": ms(PT_START + timedelta(minutes=5)),
}


def fake_zoho():
    zoho = MagicMock()
    zoho.download_recording.return_value = "video/mp4"
    zoho.delete_recording.return_value = True
    return zoho


def fake_lms():
    lms = MagicMock()
    lms.add_session_recording.return_value = 77
    return lms


def catalog():
    """Jane teaches a full-time cohort (1) and a part-time cohort (2)."""
    def s(sid, cid, name, ctype, start, key):
        return routing.Session(sid, cid, name, ctype, "Frontend", start,
                               start + timedelta(hours=2), key, "jane@school.com")
    return routing.Catalog([
        s(11, 1, "FT Cohort 4", "full-time", FT_START, "1111111111"),
        s(21, 2, "PT Cohort 5", "part-time", PT_START, "2222222222"),
    ])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(config, "DOWNLOAD_DIR", Path(self.tmp.name)),
                  patch("pipeline.notify.alert"),
                  patch("sys.stdout", new_callable=MagicMock)):
            p.start()
            self.addCleanup(p.stop)
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.catalog = catalog()
        for cid, (name, ctype) in self.catalog.cohorts.items():
            store.upsert_cohort(self.conn, cid, name, ctype)
        store.set_cohort_playlist(self.conn, 2, "PL-PT5")
        self.lms = fake_lms()
        self.yt_mocks = {
            "upload_video": MagicMock(return_value="vid-1"),
            "add_to_playlist": MagicMock(),
            "video_exists": MagicMock(return_value=True),
            "create_playlist": MagicMock(return_value="PL-NEW"),
        }
        yt = patch.multiple("pipeline.youtube_client", **self.yt_mocks)
        yt.start()
        self.addCleanup(yt.stop)

    def process(self, zoho, rec=REC):
        return pipeline.process_recording(self.conn, zoho, object(), self.lms, self.catalog, rec)


class PipelineTests(Base):
    def test_routes_to_cohort_playlist_links_lms_then_deletes(self):
        zoho = fake_zoho()
        self.assertEqual(self.process(zoho), "done")
        title = self.yt_mocks["upload_video"].call_args.args[2]
        self.assertEqual(title, "PT Cohort 5 | React hooks (2025-10-13)")
        self.yt_mocks["add_to_playlist"].assert_called_once_with(unittest.mock.ANY, "vid-1", "PL-PT5")
        self.lms.add_session_recording.assert_called_once_with(
            21, "https://youtu.be/vid-1", "React hooks (2025-10-13)")
        zoho.delete_recording.assert_called_once_with("rec-1")
        row = store.get_recording(self.conn, "rec-1")
        self.assertEqual((row["cohort_id"], row["session_id"], row["lms_recording_id"]), (2, 21, "77"))
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_same_mentor_full_time_class_goes_to_full_time_cohort(self):
        rec = dict(REC, meetingKey=None, startTimeInMs=ms(FT_START + timedelta(minutes=3)))
        self.assertEqual(self.process(fake_zoho(), rec), "done")
        self.yt_mocks["create_playlist"].assert_called_once()
        self.assertEqual(store.get_cohort(self.conn, 1)["playlist_id"], "PL-NEW")
        self.assertEqual(self.lms.add_session_recording.call_args.args[0], 11)

    def test_unmatched_recording_is_kept_in_zoho(self):
        zoho = fake_zoho()
        rec = dict(REC, creatorEmail="nobody@school.com", meetingKey="999")
        self.assertEqual(self.process(zoho, rec), "needs_review")
        zoho.download_recording.assert_not_called()
        zoho.delete_recording.assert_not_called()

    def test_admin_assignment_releases_recording(self):
        zoho = fake_zoho()
        rec = dict(REC, creatorEmail="guest@school.com", meetingKey=None)
        self.assertEqual(self.process(zoho, rec), "needs_review")
        store.assign_recording(self.conn, "rec-1", 1, 11)
        self.assertEqual(self.process(zoho, rec), "done")
        self.assertEqual(self.lms.add_session_recording.call_args.args[0], 11)

    def test_cohort_only_assignment_skips_lms_link(self):
        zoho = fake_zoho()
        rec = dict(REC, creatorEmail="guest@school.com", meetingKey=None)
        self.process(zoho, rec)
        store.assign_recording(self.conn, "rec-1", 2)
        self.assertEqual(self.process(zoho, rec), "done")
        self.lms.add_session_recording.assert_not_called()

    def test_lms_failure_keeps_zoho_copy_and_does_not_reupload(self):
        zoho = fake_zoho()
        self.lms.add_session_recording.side_effect = [RuntimeError("LMS down"), 5]
        self.assertEqual(self.process(zoho), "error")
        zoho.delete_recording.assert_not_called()
        self.assertEqual(self.process(zoho), "done")
        self.yt_mocks["upload_video"].assert_called_once()

    def test_failed_delete_does_not_reupload_or_relink_on_next_run(self):
        zoho = fake_zoho()
        zoho.delete_recording.side_effect = [RuntimeError("zoho down"), True]
        self.assertEqual(self.process(zoho), "error")
        self.assertEqual(self.process(zoho), "done")
        self.yt_mocks["upload_video"].assert_called_once()
        self.yt_mocks["add_to_playlist"].assert_called_once()
        self.lms.add_session_recording.assert_called_once()

    def test_not_deleted_when_video_missing_on_youtube(self):
        zoho = fake_zoho()
        self.yt_mocks["video_exists"].return_value = False
        self.assertEqual(self.process(zoho), "error")
        zoho.delete_recording.assert_not_called()
        self.lms.add_session_recording.assert_not_called()

    def test_gives_up_after_max_attempts(self):
        zoho = fake_zoho()
        zoho.download_recording.side_effect = RuntimeError("boom")
        with patch.object(config, "MAX_ATTEMPTS", 2):
            self.process(zoho)
            self.process(zoho)
            self.assertEqual(self.process(zoho), "failed")
        self.assertEqual(zoho.download_recording.call_count, 2)
        store.retry_recording(self.conn, "rec-1")
        self.assertEqual(store.get_recording(self.conn, "rec-1")["status"], "pending")

    def test_quota_stops_cycle_without_counting_failure(self):
        zoho = fake_zoho()
        zoho.list_recordings.return_value = [REC, dict(REC, erecordingId="rec-2")]
        self.yt_mocks["upload_video"].side_effect = QuotaExceeded("quota")
        with patch("pipeline.routing.Catalog.from_lms", return_value=self.catalog):
            pipeline.run_cycle(self.conn, zoho=zoho, youtube=object(), lms=self.lms)
        self.assertEqual(zoho.download_recording.call_count, 1)
        self.assertEqual(store.get_recording(self.conn, "rec-1")["attempts"], 0)

    def test_lms_outage_skips_cycle_and_keeps_recordings(self):
        zoho = fake_zoho()
        zoho.list_recordings.return_value = [REC]
        with patch("pipeline.routing.Catalog.from_lms", side_effect=RuntimeError("503")):
            self.assertEqual(pipeline.run_cycle(self.conn, zoho=zoho, youtube=object(), lms=self.lms), {})
        zoho.download_recording.assert_not_called()

    def test_cycle_without_new_recordings_does_not_call_lms(self):
        zoho = fake_zoho()
        zoho.list_recordings.return_value = [{"erecordingId": "x"}]
        self.assertEqual(pipeline.run_cycle(self.conn, zoho=zoho, youtube=object(), lms=self.lms), {})
        self.lms.active_cohorts.assert_not_called()

    def test_recording_without_download_url_skipped(self):
        self.assertEqual(self.process(fake_zoho(), {"erecordingId": "x"}), "skipped")


class StoreMigrationTests(unittest.TestCase):
    def test_old_database_gains_lms_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            old = sqlite3.connect(path)
            old.executescript(
                "CREATE TABLE recordings (erecording_id TEXT PRIMARY KEY, topic TEXT, mentor_key TEXT,"
                " recorded_at TEXT, status TEXT NOT NULL, video_id TEXT, playlist_id TEXT,"
                " attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, updated_at TEXT NOT NULL);"
                "INSERT INTO recordings (erecording_id, status, updated_at) VALUES ('r', 'needs_mapping', 'x');")
            old.close()
            conn = store.connect(path)
            row = store.get_recording(conn, "r")
            self.assertEqual(row["status"], "needs_review")
            self.assertIsNone(row["session_id"])
            conn.close()


class ZohoClientTests(unittest.TestCase):
    def _token_response(self, token="t1"):
        r = MagicMock(status_code=200)
        r.json.return_value = {"access_token": token, "expires_in": 3600}
        return r

    def test_token_is_cached_and_urls_include_zsoid(self):
        session = MagicMock()
        session.post.return_value = self._token_response()
        listing = MagicMock(status_code=200)
        listing.json.return_value = {"recordings": [REC]}
        session.request.return_value = listing
        client = ZohoClient(session)
        with patch.object(config, "ZOHO_ZSOID", "org9"):
            client.list_recordings()
            client.list_recordings()
        self.assertEqual(session.post.call_count, 1)
        self.assertIn("/org9/recordings.json", session.request.call_args.args[1])

    def test_expired_token_refreshes(self):
        session = MagicMock()
        session.post.side_effect = [self._token_response("t1"), self._token_response("t2")]
        client = ZohoClient(session)
        self.assertEqual(client.access_token(), "t1")
        client._token_expires_at = time.time() - 1
        self.assertEqual(client.access_token(), "t2")

    def test_401_retries_once_with_fresh_token(self):
        session = MagicMock()
        session.post.side_effect = [self._token_response("t1"), self._token_response("t2")]
        session.request.side_effect = [MagicMock(status_code=401), MagicMock(status_code=204)]
        with patch.object(config, "ZOHO_ZSOID", "org9"):
            self.assertTrue(ZohoClient(session).delete_recording("rec-1"))
        self.assertIn("t2", session.request.call_args.kwargs["headers"]["Authorization"])

    def test_mentor_keys_normalised(self):
        self.assertEqual(mentor_keys({"creatorEmail": " A@B.com ", "hostName": "Ann"}), ["a@b.com", "ann"])


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_webhook_requires_secret(self):
        with patch.object(config, "WEBHOOK_SECRET", "s3cret"):
            self.assertEqual(self.client.post("/webhook/zoho?token=bad").status_code, 403)
            self.assertEqual(self.client.post("/webhook/zoho?token=s3cret").status_code, 200)

    def test_empty_secret_never_matches(self):
        with patch.object(config, "WEBHOOK_SECRET", ""):
            self.assertEqual(self.client.post("/webhook/zoho?token=").status_code, 403)

    def test_dashboard_requires_login_and_mentor_cannot_edit(self):
        with patch.object(config, "DASHBOARD_PASSWORD", "m"), patch.object(config, "ADMIN_PASSWORD", "a"):
            self.assertEqual(self.client.get("/").status_code, 401)
            self.assertEqual(self.client.get("/", auth=("mentor", "m")).status_code, 200)
            self.assertEqual(self.client.post("/run", auth=("mentor", "m")).status_code, 401)
            self.assertEqual(self.client.post("/run", auth=("admin", "a")).status_code, 302)

    def test_admin_assigns_recording_and_sets_playlist(self):
        conn = store.connect(":memory:")
        self.addCleanup(conn.close)
        store.upsert_cohort(conn, 2, "PT Cohort 5", "part-time")
        store.ensure_recording(conn, "rec-9", "Hooks", "guest@school.com", "2025-10-13")
        store.update_recording(conn, "rec-9", status="needs_review")
        with patch.object(server, "conn", conn), patch.object(config, "ADMIN_PASSWORD", "a"), \
                patch.object(config, "DASHBOARD_PASSWORD", "m"):
            page = self.client.get("/", auth=("admin", "a")).get_data(as_text=True)
            self.assertIn("PT Cohort 5", page)
            self.assertEqual(self.client.post("/recordings/rec-9/assign", auth=("mentor", "m"),
                                              data={"cohort_id": "2"}).status_code, 401)
            self.assertEqual(self.client.post("/recordings/rec-9/assign", auth=("admin", "a"),
                                              data={"cohort_id": "2", "session_id": "x"}).status_code, 400)
            self.client.post("/recordings/rec-9/assign", auth=("admin", "a"),
                             data={"cohort_id": "2", "session_id": "21"})
            self.client.post("/cohorts/2/playlist", auth=("admin", "a"),
                             data={"playlist_id": "https://www.youtube.com/playlist?list=PLx"})
        row = store.get_recording(conn, "rec-9")
        self.assertEqual((row["status"], row["cohort_id"], row["session_id"], row["match_reason"]),
                         ("pending", 2, 21, "manual"))
        self.assertEqual(store.get_cohort(conn, 2)["playlist_id"], "PLx")

    def test_playlist_url_is_accepted(self):
        self.assertEqual(server._playlist_id("https://www.youtube.com/playlist?list=PLabc&si=x"), "PLabc")


class ZohoConfigCheckerTests(unittest.TestCase):
    @patch("check_zoho.ZOHO_REFRESH_TOKEN", "")
    @patch("check_zoho.requests.post")
    def test_missing_credentials_stop_before_network_request(self, post):
        with patch("sys.stdout", new_callable=MagicMock):
            self.assertIsNone(check_zoho.find_correct_zoho_config())
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
