"""Check Windows credential storage and model settings in disposable user profiles."""

import os
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from school_calendar import assistant_config as config
from school_calendar.storage import Store


class AssistantProfileTests(unittest.TestCase):
    def test_existing_assistant_key_and_model_preferences_survive_product_rename(self):
        from school_calendar.sync_crypto import dpapi
        import json
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
            self.assertEqual(config.directory().name, "CalendarAssistant")
            legacy = Path(tmp) / "CalenderAssistant"
            legacy.mkdir()
            prefs = {**config.DEFAULTS, "mode": "GPT-5.4"}
            (legacy / "settings.json").write_text(json.dumps(prefs), encoding="utf-8")
            (legacy / "project.key.dpapi").write_bytes(dpapi(b"fictional-pre-rename-key"))
            self.assertEqual(config.project_key(), "fictional-pre-rename-key")
            self.assertEqual(config.options(), prefs)
            config.save_key("fictional-updated-key")
            self.assertEqual(dpapi((legacy / "project.key.dpapi").read_bytes(), unlock=True),
                             b"fictional-updated-key")

    def test_replace_remove_key_and_persist_model_selection_outside_calendar(self):
        with (
            TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "LOCALAPPDATA": temporary,
                    "OPENAI_API_KEY": "sample-environment-key",
                },
            ),
        ):
            path = Path(temporary) / "SchoolCalendar" / "calendar.sqlite3"
            with closing(Store(path)):
                pass
            saved_calendar = path.read_bytes()
            self.assertEqual(config.project_key(), "sample-environment-key")
            self.assertEqual(config.options()["mode"], "Auto")
            self.assertEqual(config.options()["routine"], "gpt-6-luna")
            self.assertEqual(config.options()["planning"], "gpt-6.1-sol")

            for secret in ("sample-first-project-secret", "sample-replacement-project-secret"):
                config.save_key(secret)
                self.assertEqual(config.project_key(), secret)
                for stored_file in config.directory().iterdir():
                    self.assertNotIn(secret.encode(), stored_file.read_bytes())

            config.save_options({**config.options(), "mode": "GPT-5.4"})
            self.assertEqual(config.options()["mode"], "GPT-5.4")
            config.remove_key()
            self.assertEqual(config.project_key(), "sample-environment-key")
            self.assertEqual(path.read_bytes(), saved_calendar)
