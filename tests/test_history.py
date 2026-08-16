import unittest

from api.history import EventHistory


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class EventHistoryTests(unittest.TestCase):
    def test_history_is_bounded_by_count_and_reports_gap(self):
        history = EventHistory(max_events=2, max_bytes=4096, ttl_seconds=60, clock=FakeClock())
        history.append("thread", {"event": "one"})
        history.append("thread", {"event": "two"})
        history.append("thread", {"event": "three"})
        snapshot = history.replay("thread")
        self.assertEqual([event["event"] for event in snapshot.events], ["two", "three"])
        self.assertEqual(snapshot.first_available_seq, 1)
        self.assertEqual(snapshot.next_seq, 3)
        self.assertEqual(snapshot.dropped_events, 1)

    def test_utf8_byte_limit_and_oversized_event(self):
        clock = FakeClock()
        sample = {"event": "✓" * 3}
        limit = EventHistory.encoded_size({**sample, "seq": 0})
        history = EventHistory(max_events=10, max_bytes=limit, ttl_seconds=60, clock=clock)
        history.append("thread", sample)
        history.append("thread", {"event": "x" * limit})
        snapshot = history.replay("thread")
        self.assertEqual(len(snapshot.events), 1)
        self.assertEqual(snapshot.events[0]["event"], sample["event"])
        self.assertEqual(snapshot.dropped_events, 1)

    def test_ttl_cleanup_does_not_affect_other_thread(self):
        clock = FakeClock()
        history = EventHistory(max_events=10, max_bytes=4096, ttl_seconds=10, clock=clock)
        history.append("old", {"event": "old"})
        clock.advance(9)
        history.append("new", {"event": "new"})
        clock.advance(1)
        self.assertEqual(history.replay("old").events, ())
        self.assertEqual([event["event"] for event in history.replay("new").events], ["new"])

    def test_clear_is_explicit_and_scoped(self):
        history = EventHistory(max_events=10, max_bytes=4096, ttl_seconds=60, clock=FakeClock())
        history.append("a", {"event": "a"})
        history.append("b", {"event": "b"})
        self.assertEqual(history.clear("a"), 1)
        self.assertEqual(history.replay("a").events, ())
        self.assertEqual([event["event"] for event in history.replay("b").events], ["b"])


if __name__ == "__main__":
    unittest.main()
