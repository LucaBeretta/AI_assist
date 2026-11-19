"""Project persistence, inherited environments, filtering and complete backups."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import test_webui_app as fixtures
from project_store import ProjectStore, ProjectStoreError
from webui.app import Api


class ProjectTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_project_chat_inherits_environment_instructions_folder_and_pins(self):
        folder = self.store.directory / "source"
        folder.mkdir()
        env = {"backend": "Ollama", "model": "fake:7b", "context_length": 32768, "instructions": "Base rules",
               "pinned_paths": ["base.py"], "memory_ids": ["memory-1"]}
        project = self.api.create_project("Work", "Project rules", str(folder), ["main.py"], env)["project"]
        chat = self.api.new_chat(project_id=project["id"])["chat"]
        self.assertEqual(chat["project_id"], project["id"])
        self.assertEqual(chat["project_path"], str(folder.resolve()))
        self.assertEqual(chat["environment"]["instructions"], "Base rules\n\nProject rules")
        self.assertEqual(chat["environment"]["pinned_paths"], ["base.py", "main.py"])
        self.assertEqual((chat["model"], chat["context_length"]), ("fake:7b", 32768))
        private = self.api.new_chat(incognito=True, project_id=project["id"])["chat"]
        self.assertEqual(private["environment"]["memory_ids"], [])
        unrelated = self.api.new_chat()["chat"]
        self.assertEqual({item["id"] for item in self.api.list_chats(project["id"])["chats"]}, {chat["id"], private["id"]})
        self.assertEqual(len(self.store.list_chats(project_id=project["id"])), 1)
        self.assertEqual(len(self.api.list_chats()["chats"]), 3)
        same = self.api.new_chat_same_environment(chat["id"])["chat"]
        self.assertEqual(same["project_id"], project["id"])
        self.assertEqual(same["environment"], chat["environment"])
        self.api.update_project(project["id"], {"instructions": "New rules"})
        self.assertEqual(self.api.open_chat(chat["id"])["chat"]["environment"], chat["environment"])
        self.assertIn("New rules", self.api.new_chat(project_id=project["id"])["chat"]["environment"]["instructions"])

    def test_projects_and_chats_export_and_reload_together(self):
        project = self.api.create_project("Δ project", "Rules")["project"]
        chat = self.api.new_chat(project_id=project["id"])["chat"]
        path = self.store.directory / "backup.zip"
        self.store.export_all(path)
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(json.loads(archive.read(f"projects/{project['id']}.json")), project)
            exported = json.loads(archive.read(f"chats/{chat['id']}.json"))
            self.assertEqual(exported["project_id"], project["id"])
        restarted = Api(self.events, self.store, self.engine)
        self.addCleanup(restarted._close)
        self.assertEqual(restarted.list_projects()["projects"], [project])
        self.assertEqual(restarted.open_project(project["id"])["project"], project)

    def test_atomic_failure_bad_records_and_invalid_paths(self):
        project = self.api.create_project("Work")["project"]
        with patch("project_store.os.replace", side_effect=OSError("disk full")):
            self.assertFalse(self.api.update_project(project["id"], {"name": "Changed"})["ok"])
        self.assertEqual(self.api.open_project(project["id"])["project"], project)
        for changes in ({"pinned_paths": ["../secret"]}, {"pinned_paths": [".env"]},
                        {"default_environment": {}}, {"name": ""}, {"id": "bad"}):
            self.assertFalse(self.api.update_project(project["id"], changes)["ok"], changes)
        self.assertFalse(self.api.open_project("../escape")["ok"])
        self.assertFalse(self.api.list_chats("../escape")["ok"])
        path = self.api._projects.directory / (project["id"] + ".json")
        path.write_text("bad json")
        self.assertEqual(self.api.list_projects()["projects"], [])
        self.assertTrue(self.api.list_projects()["errors"])
        self.assertFalse(self.api.update_project(project["id"], {"name": "do not overwrite"})["ok"])
        self.assertEqual(path.read_text(), "bad json")

    def test_deleting_project_preserves_its_chats_and_environment(self):
        project = self.api.create_project("Work", "Keep rules")["project"]
        chat = self.api.new_chat(project_id=project["id"])["chat"]
        self.assertTrue(self.api.delete_project(project["id"])["ok"])
        self.assertEqual(self.api.open_chat(chat["id"])["chat"], chat)
        self.assertEqual(self.api.list_projects()["projects"], [])
        self.assertFalse(self.api.new_chat(project_id=project["id"])["ok"])


if __name__ == "__main__":
    unittest.main()
