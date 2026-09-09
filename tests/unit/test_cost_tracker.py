"""
Unit tests for Provider-Independent Cost Model (Phase 14.2).
"""

import unittest
from brain.analytics.cost_tracker import CostTracker, ModelPricing, PricingTier


class TestCostTracker(unittest.TestCase):
    """Test cost estimation with known pricing, free tier, unknown pricing handling without inventing prices."""

    def setUp(self):
        self.tracker = CostTracker()

    def test_known_pricing_calculation(self):
        """Test pricing calculation using input_per_1m and output_per_1m."""
        pricing = ModelPricing(
            model_id="gpt-4o",
            tier=PricingTier.PAID,
            input_per_1m=5.0,
            output_per_1m=15.0
        )
        self.tracker.register_pricing(pricing)

        # 100,000 input tokens = $0.50
        # 50,000 output tokens = $0.75
        # Total = $1.25
        est = self.tracker.estimate_cost("gpt-4o", input_tokens=100000, output_tokens=50000)
        self.assertIsNotNone(est)
        self.assertAlmostEqual(est.total_cost, 1.25, places=4)
        self.assertAlmostEqual(est.input_cost, 0.50, places=4)
        self.assertAlmostEqual(est.output_cost, 0.75, places=4)

    def test_free_and_local_models(self):
        """Test that free/local models return zero cost, not unknown."""
        pricing = ModelPricing(
            model_id="ollama-llama3",
            tier=PricingTier.FREE,
            input_per_1m=0.0,
            output_per_1m=0.0
        )
        self.tracker.register_pricing(pricing)
        est = self.tracker.estimate_cost("ollama-llama3", input_tokens=500000, output_tokens=500000)
        self.assertIsNotNone(est)
        self.assertEqual(est.total_cost, 0.0)

    def test_unknown_pricing_never_invents_prices(self):
        """When model pricing is unknown, do not invent prices; return known=False or None."""
        est = self.tracker.estimate_cost("unregistered-proprietary-model-x", input_tokens=10000, output_tokens=5000)
        self.assertFalse(est.is_known)
        self.assertEqual(est.total_cost, 0.0)

    def test_custom_pricing_configuration(self):
        """Test custom enterprise contract pricing."""
        pricing = ModelPricing(
            model_id="claude-3-5-sonnet-custom",
            tier=PricingTier.CUSTOM,
            input_per_1m=2.5,
            output_per_1m=10.0
        )
        self.tracker.register_pricing(pricing)
        est = self.tracker.estimate_cost("claude-3-5-sonnet-custom", input_tokens=1000000, output_tokens=1000000)
        self.assertEqual(est.total_cost, 12.5)


if __name__ == "__main__":
    unittest.main()
