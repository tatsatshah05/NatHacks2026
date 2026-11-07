"""Purpose: check the ice-risk rule with controlled temperatures and moisture.

These tests cover freezing/dew-point boundaries and invalid input values.
They use no network calls and can run with either unittest or pytest.
"""

import unittest

from cloud.app.weather import evaluate_weather_watch


class WeatherWatchTests(unittest.TestCase):
    def test_rule_branches_and_boundaries(self) -> None:
        baseline = {
            "air_c": -2.0,
            "ground_c": -1.0,
            "dew_c": -5.0,
            "precipitation_last_6h_mm": 0.0,
            "max_air_last_12h_c": 0.0,
        }
        cases = [
            ("cold and dry", {}, False),
            ("recent precipitation", {"precipitation_last_6h_mm": 0.1}, True),
            ("melt and refreeze", {"max_air_last_12h_c": 0.1}, True),
            ("dew point boundary", {"dew_c": -3.0}, True),
            ("outside dew point boundary", {"dew_c": -3.01}, False),
            (
                "warm despite rain",
                {"air_c": 2.0, "ground_c": 1.0, "precipitation_last_6h_mm": 1.0},
                False,
            ),
            (
                "ground freezing boundary",
                {"air_c": 2.0, "ground_c": 0.0, "precipitation_last_6h_mm": 1.0},
                True,
            ),
            (
                "air freezing boundary",
                {"air_c": 0.0, "ground_c": 2.0, "precipitation_last_6h_mm": 1.0},
                True,
            ),
        ]
        for name, changes, expected in cases:
            with self.subTest(name=name):
                decision = evaluate_weather_watch(**{**baseline, **changes})
                self.assertEqual(decision.active, expected)
                self.assertTrue(decision.reason)

    def test_rejects_invalid_source_values(self) -> None:
        for invalid in (float("nan"), float("inf"), -1.0):
            with self.subTest(precipitation=invalid), self.assertRaises(ValueError):
                evaluate_weather_watch(
                    air_c=-2.0,
                    ground_c=-1.0,
                    dew_c=-5.0,
                    precipitation_last_6h_mm=invalid,
                    max_air_last_12h_c=0.0,
                )
