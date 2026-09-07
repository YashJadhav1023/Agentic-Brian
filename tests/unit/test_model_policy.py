"""Unit tests for deterministic model selection, tiers, and verification."""
import unittest

from models.policies.model_policy import (
    Complexity,
    ModelTier,
    select_model,
    verify_actual_model,
)


class TestModelPolicy(unittest.TestCase):

    def test_frontier_selection_on_reasoning(self):
        dec_kiro = select_model("kiro-cli", complexity=Complexity.REASONING)
        self.assertEqual(dec_kiro.tier, ModelTier.FRONTIER)
        self.assertEqual(dec_kiro.preferred_model, "claude-opus-5")

        dec_a1 = select_model("antigravity-account-1", complexity=Complexity.REASONING)
        self.assertEqual(dec_a1.tier, ModelTier.FRONTIER)
        self.assertEqual(dec_a1.preferred_model, "claude-opus-4-6-thinking")

        dec_a2 = select_model("antigravity-account-2", complexity=Complexity.REASONING)
        self.assertEqual(dec_a2.tier, ModelTier.FRONTIER)
        self.assertEqual(dec_a2.preferred_model, "gemini-3.1-pro-high")

    def test_fast_selection_on_fast(self):
        dec_kiro = select_model("kiro-cli", complexity=Complexity.FAST)
        self.assertEqual(dec_kiro.tier, ModelTier.FAST)
        self.assertEqual(dec_kiro.preferred_model, "claude-haiku-4.5")

        dec_a1 = select_model("antigravity-account-1", complexity=Complexity.FAST)
        self.assertEqual(dec_a1.tier, ModelTier.FAST)
        self.assertEqual(dec_a1.preferred_model, "gemini-3.8-flash-low")

    def test_verify_actual_model_never_assumes(self):
        # When payload has explicit model
        self.assertEqual(verify_actual_model({"actual_model": "gemini-3.8-flash-low"}, "claude-opus-5"), "gemini-3.8-flash-low")
        # When payload has raw_response
        self.assertEqual(verify_actual_model({"raw_response": {"model": "gemini-3.8-flash-medium"}}, None), "gemini-3.8-flash-medium")


if __name__ == "__main__":
    unittest.main()
