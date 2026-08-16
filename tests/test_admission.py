import unittest

from api.admission import AdmissionError, AdmissionSettings, TaskAdmission


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class AdmissionTests(unittest.TestCase):
    def make_admission(self, clock, **kwargs):
        settings = AdmissionSettings(
            max_active_per_user=kwargs.get("max_active_per_user", 2),
            max_active_process=kwargs.get("max_active_process", 8),
            rate_limit=kwargs.get("rate_limit", 10),
            rate_window_seconds=kwargs.get("rate_window_seconds", 60),
            rate_history_ttl_seconds=kwargs.get("rate_history_ttl_seconds", 120),
            max_query_bytes=1024,
        )
        return TaskAdmission(settings, clock=clock)

    def test_user_and_process_limits_are_conflicts(self):
        clock = FakeClock()
        admission = self.make_admission(clock, max_active_per_user=1, max_active_process=2)
        first = admission.acquire("user-a", "thread-a")
        with self.assertRaisesRegex(AdmissionError, "user active") as user_error:
            admission.acquire("user-a", "thread-b")
        self.assertEqual(user_error.exception.status_code, 409)

        second = admission.acquire("user-b", "thread-b")
        with self.assertRaisesRegex(AdmissionError, "process active") as process_error:
            admission.acquire("user-c", "thread-c")
        self.assertEqual(process_error.exception.status_code, 409)
        first.release()
        second.release()
        self.assertEqual(admission.active_process, 0)

    def test_thread_conflict_and_idempotent_release(self):
        admission = self.make_admission(FakeClock())
        lease = admission.acquire("user-a", "thread-a")
        with self.assertRaisesRegex(AdmissionError, "thread already"):
            admission.acquire("user-b", "thread-a")
        lease.release()
        lease.release()
        replacement = admission.acquire("user-b", "thread-a")
        replacement.release()

    def test_rate_limit_resets_at_window_boundary(self):
        clock = FakeClock()
        admission = self.make_admission(clock, rate_limit=2, rate_window_seconds=10)
        admission.acquire("user-a", "thread-a").release()
        admission.acquire("user-a", "thread-b").release()
        with self.assertRaises(AdmissionError) as limited:
            admission.acquire("user-a", "thread-c")
        self.assertEqual(limited.exception.status_code, 429)
        self.assertEqual(limited.exception.retry_after, 10)
        clock.advance(10)
        admission.acquire("user-a", "thread-c").release()

    def test_rate_history_cleanup_keeps_active_user(self):
        clock = FakeClock()
        admission = self.make_admission(clock, rate_history_ttl_seconds=5)
        lease = admission.acquire("user-a", "thread-a")
        clock.advance(6)
        self.assertEqual(admission.cleanup_idle_users(), 0)
        lease.release()
        clock.advance(5)
        self.assertEqual(admission.cleanup_idle_users(), 1)


if __name__ == "__main__":
    unittest.main()
