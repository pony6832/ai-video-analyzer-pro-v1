import unittest

from app.video_utils import clamp_seconds, hhmmss_to_seconds, seconds_to_timecode


class TimecodeTests(unittest.TestCase):
    def test_parses_mm_ss(self) -> None:
        self.assertEqual(hhmmss_to_seconds("01:05"), 65)

    def test_parses_hh_mm_ss(self) -> None:
        self.assertEqual(hhmmss_to_seconds("01:02:03"), 3723)

    def test_parses_milliseconds(self) -> None:
        self.assertAlmostEqual(hhmmss_to_seconds("00:01:02.345"), 62.345)

    def test_parses_wrapped_timecode(self) -> None:
        self.assertEqual(hhmmss_to_seconds("[00:02:10]"), 130)

    def test_formats_milliseconds(self) -> None:
        self.assertEqual(seconds_to_timecode(62.345), "00:01:02.345")

    def test_clamps_to_duration(self) -> None:
        self.assertEqual(clamp_seconds(125, 90), 90)
        self.assertEqual(clamp_seconds(-5, 90), 0)

    def test_rejects_empty_value(self) -> None:
        with self.assertRaises(ValueError):
            hhmmss_to_seconds("")


if __name__ == "__main__":
    unittest.main()
