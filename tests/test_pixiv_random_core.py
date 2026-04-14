from __future__ import annotations

import random
import unittest

from pixiv_random_core import filter_candidates, normalize_pixiv_entries, pick_random_entry


class PixivRandomCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = [
            {
                "id": 100,
                "part": 0,
                "title": "safe image",
                "ext": "png",
                "author": {"id": 10, "name": "Alice"},
                "sanity_level": 2,
                "x_restrict": 0,
                "bookmark": 50,
                "created_at": "2026-01-01T00:00:00+09:00",
            },
            {
                "id": 101,
                "part": 1,
                "title": "unsafe image",
                "ext": "jpg",
                "author": {"id": 11, "name": "Bob"},
                "sanity_level": 6,
                "x_restrict": 1,
                "bookmark": 20,
                "created_at": "2026-01-02T00:00:00+09:00",
            },
        ]

    def test_normalize_builds_preview_and_original_urls(self) -> None:
        entries = normalize_pixiv_entries(
            self.payload,
            "https://example.com/image/preview",
            "https://example.com/image/original",
        )

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].preview_url, "https://example.com/image/preview/100_p0.webp")
        self.assertEqual(entries[0].original_url, "https://example.com/image/original/100_p0.png")
        self.assertEqual(entries[0].pixiv_artwork_url, "https://www.pixiv.net/artworks/100")

    def test_safe_filter_excludes_r18_and_high_sanity(self) -> None:
        entries = normalize_pixiv_entries(self.payload, "https://example.com/image/preview")
        filtered = filter_candidates(entries, safe_mode=True, max_sanity_level=4, recent_keys=set())

        self.assertEqual([entry.id for entry in filtered], [100])

    def test_recent_filter_excludes_recent_keys(self) -> None:
        entries = normalize_pixiv_entries(self.payload, "https://example.com/image/preview")
        filtered = filter_candidates(entries, safe_mode=False, max_sanity_level=6, recent_keys={"100:0"})

        self.assertEqual([entry.key for entry in filtered], ["101:1"])

    def test_pick_random_entry_returns_deterministic_choice_with_rng(self) -> None:
        entries = normalize_pixiv_entries(self.payload, "https://example.com/image/preview")
        chosen = pick_random_entry(entries, rng=random.Random(0))

        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.id, 101)


if __name__ == "__main__":
    unittest.main()
