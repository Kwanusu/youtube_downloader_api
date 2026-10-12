import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import check_zoho
import config
import pipeline
import server
import store
from youtube_client import QuotaExceeded
from zoho_client import ZohoClient, mentor_keys

REC = {
    "erecordingId": "rec-1",
    "downloadUrl": "https://example.test/video",
    "topic": "React hooks",
    "creatorEmail": "Jane@School.com",
    "startTimeInMs": 1760000000000,
}


def fake_zoho():
    zoho = MagicMock()
    zoho.download_recording.return_value = "video/mp4"
    zoho.delete_recording.return_value = True
    return zoho


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
        store.upsert_mentor(self.conn, "jane@school.com", "Jane", "Web Dev C4", "PL123")
        self.yt_mocks = {
            "upload_video": MagicMock(return_value="vid-1"),
            "add_to_playlist": MagicMock(),
            "video_exists": MagicMock(return_value=True),
        }
        yt = patch.multiple("pipeline.youtube_client", **self.yt_mocks)
        yt.start()
        self.addCleanup(yt.stop)


class PipelineTests(Base):
    def test_routes_to_mentor_playlist_then_deletes(self):
        zoho = fake_zoho()
        self.assertEqual(pipeline.process_recording(self.conn, zoho, object(), REC), "done")
        title = self.yt_mocks["upload_video"].call_args.args[2]
        self.assertIn("Web Dev C4", title)
        self.assertIn("React hooks", title)
        self.yt_mocks["add_to_playlist"].assert_called_once_with(unittest.mock.ANY, "vid-1", "PL123")
        zoho.delete_recording.assert_called_once_with("rec-1")
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_unknown_mentor_is_kept_in_zoho(self):
        zoho = fake_zoho()
        rec = dict(REC, creatorEmail="nobody@school.com")
        self.assertEqual(pipeline.process_recording(self.conn, zoho, object(), rec), "needs_mapping")
        zoho.download_recording.assert_not_called()
        zoho.delete_recording.assert_not_called()

    def test_mapping_mentor_later_releases_recording(self):
        zoho = fake_zoho()
        rec = dict(REC, creatorEmail="new@school.com")
        pipeline.process_recording(self.conn, zoho, object(), rec)
        store.upsert_mentor(self.conn, "new@school.com", "New", "Data C1", "PL9")
        self.assertEqual(pipeline.process_recording(self.conn, zoho, object(), rec), "done")

    def test_failed_delete_does_not_reupload_on_next_run(self):
        zoho = fake_zoho()
        zoho.delete_recording.side_effect = [RuntimeError("zoho down"), True]
        self.assertEqual(pipeline.process_recording(self.conn, zoho, object(), REC), "error")
        self.assertEqual(pipeline.process_recording(self.conn, zoho, object(), REC), "done")
        self.yt_mocks["upload_video"].assert_called_once()
        self.yt_mocks["add_to_playlist"].assert_called_once()

    def test_not_deleted_when_video_missing_on_youtube(self):
        zoho = fake_zoho()
        self.yt_mocks["video_exists"].return_value = False
        self.assertEqual(pipeline.process_recording(self.conn, zoho, object(), REC), "error")
        zoho.delete_recording.assert_not_called()

    def test_gives_up_after_max_attempts(self):
        zoho = fake_zoho()
        zoho.download_recording.side_effect = RuntimeError("boom")
        with patch.object(config, "MAX_ATTEMPTS", 2):
            pipeline.process_recording(self.conn, zoho, object(), REC)
            pipeline.process_recording(self.conn, zoho, object(), REC)
            self.assertEqual(pipeline.process_recording(self.conn, zoho, object(), REC), "failed")
        self.assertEqual(zoho.download_recording.call_count, 2)
        store.retry_recording(self.conn, "rec-1")
        self.assertEqual(store.get_recording(self.conn, "rec-1")["status"], "pending")

    def test_quota_stops_cycle_without_counting_failure(self):
        zoho = fake_zoho()
        zoho.list_recordings.return_value = [REC, dict(REC, erecordingId="rec-2")]
        self.yt_mocks["upload_video"].side_effect = QuotaExceeded("quota")
        pipeline.run_cycle(self.conn, zoho=zoho, youtube=object())
        self.assertEqual(zoho.download_recording.call_count, 1)
        self.assertEqual(store.get_recording(self.conn, "rec-1")["attempts"], 0)

    def test_recording_without_download_url_skipped(self):
        self.assertEqual(
            pipeline.process_recording(self.conn, fake_zoho(), object(), {"erecordingId": "x"}), "skipped"
        )


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
