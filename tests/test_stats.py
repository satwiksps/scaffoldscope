from __future__ import annotations

import unittest

from scaffoldscope.stats import (
    RESAMPLING_ALGORITHM,
    bootstrap_mean_interval,
    empirical_mde,
    paired_sign_flip_pvalue,
    percentile,
    prospective_paired_mde,
)


class StatsTests(unittest.TestCase):
    def test_percentile_interpolates(self) -> None:
        self.assertEqual(percentile([0.0, 10.0], 0.5), 5.0)

    def test_bootstrap_is_deterministic(self) -> None:
        first = bootstrap_mean_interval([0.0, 0.5, 1.0], samples=500, seed=42)
        second = bootstrap_mean_interval([0.0, 0.5, 1.0], samples=500, seed=42)
        self.assertEqual(first, second)

    def test_resampling_protocol_vector_is_stable(self) -> None:
        self.assertEqual(RESAMPLING_ALGORITHM, "sha256-counter-v1")
        self.assertEqual(
            bootstrap_mean_interval([0.0, 1.0, 2.0, 3.0], samples=20, seed=17),
            (0.6187499999999999, 2.5),
        )
        self.assertEqual(
            paired_sign_flip_pvalue(
                [1.0 if index % 3 else -0.5 for index in range(30)],
                draws=1_000,
                seed=17,
            ),
            0.003996003996003996,
        )

    def test_sign_flip(self) -> None:
        self.assertIsNotNone(paired_sign_flip_pvalue([1.0, 0.0, 1.0]))

    def test_empirical_mde_is_suppressed_below_ten_tasks(self) -> None:
        self.assertIsNone(empirical_mde([0.0, 1.0] * 4 + [0.0]))
        self.assertIsNone(empirical_mde([1.0] * 10))
        self.assertIsNotNone(empirical_mde([0.0, 1.0] * 5))

    def test_prospective_mde_uses_declared_discordance(self) -> None:
        fifty_task_mde = prospective_paired_mde(50, anticipated_discordance=0.2)
        two_hundred_task_mde = prospective_paired_mde(200, anticipated_discordance=0.2)

        self.assertIsNotNone(fifty_task_mde)
        self.assertIsNotNone(two_hundred_task_mde)
        assert fifty_task_mde is not None
        assert two_hundred_task_mde is not None
        self.assertAlmostEqual(fifty_task_mde, 0.177188, places=6)
        self.assertAlmostEqual(two_hundred_task_mde, fifty_task_mde / 2, places=12)

    def test_prospective_mde_rejects_invalid_design_inputs(self) -> None:
        self.assertIsNone(prospective_paired_mde(0))
        self.assertIsNone(prospective_paired_mde(50, anticipated_discordance=0.0))
        self.assertIsNone(prospective_paired_mde(50, anticipated_discordance=1.1))


if __name__ == "__main__":
    unittest.main()
