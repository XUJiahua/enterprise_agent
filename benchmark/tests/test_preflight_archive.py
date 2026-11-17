import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/archive_preflight.py"
spec = importlib.util.spec_from_file_location("archive_preflight", SCRIPT)
archive_preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive_preflight)


class PreflightArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.trial = "given--011"
        (self.run / "plan.json").write_text(json.dumps({"trials": [{"id": self.trial}]}))
        self.result = self.run / "results" / self.trial

    def blocked(self, text="probe"):
        self.result.mkdir(parents=True)
        (self.result / "preflight.json").write_text('{"ready": false}')
        (self.result / "probe").mkdir()
        (self.result / "probe/events.jsonl").write_text(text)

    def test_repeated_same_day_blocks_preserve_every_probe_and_clear_source(self):
        snapshots = []
        # Include an old v9-style same-name archive and a slot reserved by an
        # interrupted archiver. Neither may be replaced or mistaken for success.
        root = self.run / "preflight-blocked-20261117"
        (root / self.trial).mkdir(parents=True)
        (root / self.trial / "old.txt").write_text("original")
        (root / (self.trial + "--0001")).mkdir()
        for n in range(3):
            self.blocked(str(n))
            snapshots.append(archive_preflight.archive(self.run, self.trial, "20261117"))
            self.assertFalse(self.result.exists())
        self.assertEqual(len(set(snapshots)), 3)
        self.assertEqual([(p / "probe/events.jsonl").read_text() for p in snapshots], ["0", "1", "2"])
        self.assertEqual((root / self.trial / "old.txt").read_text(), "original")

    def test_started_completed_or_ambiguous_trial_is_never_retried(self):
        self.blocked()
        for marker in ("generation", "receipt.json", "outcome.json", "candidate.bundle", "unknown"):
            with self.subTest(marker=marker):
                (self.result / marker).touch()
                with self.assertRaisesRegex(ValueError, "generation or unknown"):
                    archive_preflight.archive(self.run, self.trial)
                self.assertTrue(self.result.exists())
                (self.result / marker).unlink()
        for value in ({"ready": True}, {}, {"ready": 0}):
            (self.result / "preflight.json").write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "ready=false"):
                archive_preflight.archive(self.run, self.trial)

    def test_failed_move_retains_original_and_cli_stops_before_generation(self):
        self.blocked()
        with patch.object(archive_preflight.os, "rename", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                archive_preflight.archive(self.run, self.trial, "20261117")
        self.assertEqual((self.result / "probe/events.jsonl").read_text(), "probe")
        self.assertEqual(list((self.run / "preflight-blocked-20261117").iterdir()), [])
        (self.result / "receipt.json").touch()
        proc = subprocess.run([sys.executable, str(SCRIPT), "--run", str(self.run), "--trial", self.trial], capture_output=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.result.exists())
        marker = self.run / "generation-restarted"
        proc = subprocess.run(["bash", "-c", '"$1" "$2" --run "$3" --trial "$4" && touch "$5"',
                               "archive-test", sys.executable, str(SCRIPT), str(self.run), self.trial, str(marker)],
                              capture_output=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(marker.exists())

    def test_missing_record_unknown_trial_and_symlinks_are_refused(self):
        self.blocked()
        with self.assertRaisesRegex(ValueError, "exactly once"):
            archive_preflight.archive(self.run, "other--001")
        with self.assertRaisesRegex(ValueError, "invalid trial"):
            archive_preflight.archive(self.run, "../given--011")
        (self.result / "preflight.json").unlink()
        with self.assertRaises(FileNotFoundError):
            archive_preflight.archive(self.run, self.trial)
        (self.result / "preflight.json").symlink_to(self.run / "plan.json")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            archive_preflight.archive(self.run, self.trial)


if __name__ == "__main__":
    unittest.main()
