import time
import unittest
from unittest.mock import MagicMock, patch

import config
from lms_client import LmsClient, LmsError


def response(status=200, body=None):
    r = MagicMock(status_code=status, content=b"x" if body is not None else b"")
    r.json.return_value = body
    return r


class LmsClientTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("LMS_ORG_ID", "org-uuid"), ("LMS_CLIENT_ID", "cid"),
                            ("LMS_CLIENT_SECRET", "sec"), ("LMS_ACCESS_TOKEN", ""),
                            ("LMS_API_BASE_URL", "https://api.test")):
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.session = MagicMock()
        self.session.post.return_value = response(body={"access_token": "t1", "expires_in": 3600})

    def test_sends_bearer_and_org_header_and_caches_token(self):
        self.session.request.return_value = response(body={"cohorts": [{"id": 1}], "total_pages": 1})
        client = LmsClient(self.session)
        self.assertEqual(client.active_cohorts(), [{"id": 1}])
        client.active_cohorts()
        self.assertEqual(self.session.post.call_count, 1)
        kwargs = self.session.request.call_args.kwargs
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer t1", "X-Org-Id": "org-uuid"})
        self.assertEqual(kwargs["params"]["status"], "active")

    def test_paginates(self):
        self.session.request.side_effect = [
            response(body={"sessions": [{"session_id": 1}], "total_pages": 2}),
            response(body={"sessions": [{"session_id": 2}], "total_pages": 2}),
        ]
        rows = LmsClient(self.session).past_sessions(7)
        self.assertEqual([r["session_id"] for r in rows], [1, 2])
        self.assertIn("/cohort/7/schedule", self.session.request.call_args.args[1])

    def test_401_refreshes_token_once(self):
        self.session.post.side_effect = [response(body={"access_token": "t1"}),
                                         response(body={"access_token": "t2"})]
        self.session.request.side_effect = [response(401, {}), response(body={"recordings": []})]
        LmsClient(self.session).session_recordings(5)
        self.assertEqual(self.session.request.call_args.kwargs["headers"]["Authorization"], "Bearer t2")

    def test_static_access_token_skips_token_endpoint(self):
        self.session.request.return_value = response(body={"recordings": []})
        with patch.object(config, "LMS_ACCESS_TOKEN", "pat"):
            LmsClient(self.session).session_recordings(5)
        self.session.post.assert_not_called()

    def test_add_recording_is_idempotent(self):
        self.session.request.return_value = response(
            body={"recordings": [{"id": 9, "recording_url": "https://youtu.be/v"}]})
        self.assertEqual(LmsClient(self.session).add_session_recording(5, "https://youtu.be/v", "T"), 9)
        self.assertEqual(self.session.request.call_count, 1)  # no POST

    def test_add_recording_posts_payload(self):
        self.session.request.side_effect = [response(body={"recordings": []}), response(body={"id": 3})]
        self.assertEqual(LmsClient(self.session).add_session_recording(5, "https://youtu.be/v", "T"), 3)
        args, kwargs = self.session.request.call_args
        self.assertEqual(args[:2], ("POST", "https://api.test/event/session-recording"))
        self.assertEqual(kwargs["json"], {"session_id": 5, "recording_url": "https://youtu.be/v",
                                          "title": "T", "duration": None})

    def test_rejected_recording_raises_with_detail(self):
        self.session.request.side_effect = [response(body={"recordings": []}),
                                            response(409, {"detail": "Maximum recordings reached"})]
        with self.assertRaisesRegex(LmsError, "Maximum recordings"):
            LmsClient(self.session).add_session_recording(5, "https://youtu.be/v", "T")

    def test_missing_org_id_fails_fast(self):
        with patch.object(config, "LMS_ORG_ID", ""):
            with self.assertRaises(LmsError):
                LmsClient(self.session).session_recordings(5)
        self.session.request.assert_not_called()

    def test_expired_token_refreshes(self):
        self.session.post.side_effect = [response(body={"access_token": "t1"}),
                                         response(body={"access_token": "t2"})]
        client = LmsClient(self.session)
        self.assertEqual(client.access_token(), "t1")
        client._token_expires_at = time.time() - 1
        self.assertEqual(client.access_token(), "t2")


if __name__ == "__main__":
    unittest.main()
