import unittest

from agent.llm import _VALID_THINKING, _init_deepseek_model, model, pro_model


class DeepSeekThinkingConfigTests(unittest.TestCase):
    def test_flash_disables_thinking(self):
        self.assertEqual(model.extra_body, {"thinking": {"type": "disabled"}})

    def test_pro_enables_thinking_with_high_effort(self):
        self.assertEqual(
            pro_model.extra_body,
            {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
        )

    def test_rejects_legacy_high_thinking_type(self):
        with self.assertRaises(ValueError):
            _init_deepseek_model(
                "deepseek-v4-pro",
                thinking="high",
                temperature=0.6,
            )
        self.assertIn("enabled", _VALID_THINKING)
        self.assertNotIn("high", _VALID_THINKING)


if __name__ == "__main__":
    unittest.main()
