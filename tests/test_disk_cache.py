import tempfile
import unittest
from pathlib import Path

from fakes import FakeClock

from dota_winrates.disk_cache import MISSING, DiskCache


class DiskCacheTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.clock = FakeClock()
        self.cache = DiskCache(self.directory, clock=self.clock.time)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_missing_entry_reads_as_missing(self):
        self.assertIs(self.cache.read("matches/1.json", None), MISSING)

    def test_written_entry_is_read_back(self):
        self.cache.write("matches/1.json", {"match_id": 1})
        self.assertEqual(self.cache.read("matches/1.json", None), {"match_id": 1})

    def test_none_is_a_valid_cached_value(self):
        self.cache.write("stratz_totals/7.json", None)
        self.assertIsNone(self.cache.read("stratz_totals/7.json", 60))

    def test_entry_older_than_max_age_reads_as_missing(self):
        self.cache.write("win_loss/1.json", {"win": 1, "lose": 1})
        self.clock.now += 61
        self.assertIs(self.cache.read("win_loss/1.json", 60), MISSING)

    def test_entry_exactly_at_max_age_is_still_valid(self):
        self.cache.write("win_loss/1.json", {"win": 1, "lose": 1})
        self.clock.now += 60
        self.assertEqual(self.cache.read("win_loss/1.json", 60), {"win": 1, "lose": 1})

    def test_corrupt_file_reads_as_missing(self):
        (self.directory / "matches").mkdir()
        (self.directory / "matches" / "1.json").write_text('{"fetched_at": 1, "da', encoding="utf-8")
        self.assertIs(self.cache.read("matches/1.json", None), MISSING)

    def test_write_leaves_no_temporary_file(self):
        self.cache.write("matches/1.json", {"match_id": 1})
        self.assertEqual(sorted(path.name for path in (self.directory / "matches").iterdir()), ["1.json"])

    def test_reads_files_written_by_the_old_script(self):
        (self.directory / "heroes.json").write_text('{"fetched_at": 1000000.0, "data": [{"id": 1}]}', encoding="utf-8")
        self.assertEqual(self.cache.read("heroes.json", None), [{"id": 1}])


if __name__ == "__main__":
    unittest.main()
