import unittest
from unittest.mock import patch

from agent.llm import (
    _VALID_THINKING,
    DeepSeekSettings,
    _init_deepseek_model,
    create_model_bundle,
)


class DeepSeekThinkingConfigTests(unittest.TestCase):
    @patch("agent.llm.init_chat_model")
    def test_bundle_builds_models_only_when_requested(self, init_chat_model):
        init_chat_model.side_effect = lambda **kwargs: kwargs
        bundle = create_model_bundle(
            DeepSeekSettings("key", "https://example.test", "flash", "pro")
        )
        self.assertEqual(bundle.flash["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(
            bundle.pro["extra_body"],
            {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
        )

    def test_rejects_legacy_high_thinking_type(self):
        with self.assertRaises(ValueError):
            _init_deepseek_model(
                "deepseek-v4-pro",
                api_key="key",
                base_url="https://example.test",
                thinking="high",
                temperature=0.6,
            )
        self.assertIn("enabled", _VALID_THINKING)
        self.assertNotIn("high", _VALID_THINKING)


if __name__ == "__main__":
    unittest.main()
