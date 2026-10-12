import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import routing

UTC = timezone.utc


def ms(dt):
    return int(dt.timestamp() * 1000)


def session(sid, cohort_id, ctype, start, key=None, email="jane@school.com", minutes=120):
    return routing.Session(sid, cohort_id, f"Cohort {cohort_id}", ctype, f"Session {sid}",
                           start, start + timedelta(minutes=minutes), key, email)


# Monday 13 Oct 2025. Nairobi is UTC+3: 09:00 EAT = 06:00 UTC, 19:00 EAT = 16:00 UTC.
FT_START = datetime(2025, 10, 13, 6, 0, tzinfo=UTC)
PT_START = datetime(2025, 10, 13, 16, 0, tzinfo=UTC)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        # Jane runs a full-time cohort (1) and a part-time cohort (2) on the same day.
        self.catalog = routing.Catalog([
            session(11, 1, "full-time", FT_START, key="1111111111", minutes=300),
            session(21, 2, "part-time", PT_START, key="2222222222"),
        ])

    def resolve(self, start, **rec):
        return self.catalog.resolve(dict(rec, startTimeInMs=ms(start)), ["jane@school.com"])

    def test_meeting_key_wins(self):
        match = self.catalog.resolve({"startTimeInMs": ms(PT_START), "meetingKey": "1111111111"}, [])
        self.assertIsNone(match)  # right meeting, wrong time: not trusted
        match = self.catalog.resolve({"startTimeInMs": ms(FT_START), "meetingKey": "1111111111"}, [])
        self.assertEqual((match.cohort_id, match.session_id, match.reason), (1, 11, "meeting link"))

    def test_same_mentor_routed_by_time_of_day(self):
        self.assertEqual(self.resolve(FT_START + timedelta(minutes=5)).cohort_id, 1)
        self.assertEqual(self.resolve(PT_START + timedelta(minutes=5)).cohort_id, 2)

    def test_slot_fallback_when_session_time_drifted(self):
        # Part-time class scheduled 19:00 but recorded 21:50 EAT (outside the session window).
        match = self.resolve(PT_START + timedelta(hours=2, minutes=50))
        self.assertEqual((match.cohort_id, match.reason), (2, "mentor's part-time cohort"))

    def test_unknown_host_outside_sessions_is_unmatched(self):
        rec = {"startTimeInMs": ms(PT_START)}
        self.assertIsNone(self.catalog.resolve(rec, ["someone@else.com"]))

    def test_slots_use_nairobi_time(self):
        self.assertEqual(routing.slot_for(FT_START), routing.FULL_TIME)             # 09:00 EAT Mon
        self.assertEqual(routing.slot_for(PT_START + timedelta(hours=1)), routing.PART_TIME)  # 20:00
        self.assertIsNone(routing.slot_for(FT_START - timedelta(hours=1)))         # 08:00
        saturday = FT_START + timedelta(days=5)
        self.assertIsNone(routing.slot_for(saturday))

    def test_meeting_key_extraction(self):
        for link in ("https://meeting.zoho.com/meeting/videomeeting?key=1234567890",
                     "https://meeting.zoho.com/meeting/register?sessionId=1234567890",
                     "https://meeting.zoho.com/meeting/1234567890"):
            self.assertEqual(routing.meeting_key_from_link(link), "1234567890")
        self.assertIsNone(routing.meeting_key_from_link("https://meet.zoho.com/abcdef"))
        self.assertIsNone(routing.meeting_key_from_link(None))

    def test_cohort_type_normalised(self):
        self.assertEqual(routing.normalise_cohort_type("Part_Time"), routing.PART_TIME)
        self.assertEqual(routing.normalise_cohort_type(None), routing.FULL_TIME)

    def test_catalog_from_lms_skips_cancelled_sessions(self):
        lms = MagicMock()
        lms.active_cohorts.return_value = [{"id": 2, "name": "PT C5", "type": "part-time"}]
        lms.past_sessions.return_value = [
            {"session_id": 1, "session_date": "2025-10-13", "start_time": "16:00:00",
             "duration_minutes": 120, "meeting_link": "https://meeting.zoho.com/x?key=99999999",
             "instructor_id": "u1", "title": "Hooks"},
            {"session_id": 2, "session_date": "2025-10-14", "start_time": "16:00:00",
             "cancelled_at": "2025-10-14T10:00:00"},
        ]
        lms.user_email.return_value = "jane@school.com"
        catalog = routing.Catalog.from_lms(lms)
        self.assertEqual(len(catalog.sessions), 1)
        s = catalog.sessions[0]
        self.assertEqual((s.start, s.meeting_key, s.cohort_type), (PT_START, "99999999", "part-time"))
        self.assertEqual(catalog.cohorts[2], ("PT C5", "part-time"))


if __name__ == "__main__":
    unittest.main()
