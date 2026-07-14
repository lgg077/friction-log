import os
import tempfile
import unittest

from app import create_app


class FrictionLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = os.path.join(self.temp_dir.name, "test.db")
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test",
                "DATABASE": database_path,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_entry(self) -> None:
        self.client.post(
            "/frustrations/new",
            data={
                "title": "Manual reporting takes too long",
                "department": "Operations",
                "description": "The weekly report is assembled by hand from four systems.",
                "business_impact": "Managers wait longer for updates and errors slip through.",
                "frequency_value": "weekly",
                "pain_score": "8",
                "estimated_hours_lost": "3.5",
                "current_workaround": "One analyst merges everything in spreadsheets.",
                "ideal_outcome": "A single automated report with current numbers.",
                "status": "New",
            },
            follow_redirects=True,
        )

    def test_dashboard_loads(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Friction Log", response.data)

    def test_create_entry_shows_on_dashboard(self) -> None:
        self.create_entry()
        response = self.client.get("/")
        self.assertIn(b"Manual reporting takes too long", response.data)
        self.assertIn(b"84.0", response.data)

    def test_filters_by_status(self) -> None:
        self.create_entry()
        response = self.client.get("/?status=Resolved")
        self.assertNotIn(b"Manual reporting takes too long", response.data)

    def test_edit_entry_updates_status(self) -> None:
        self.create_entry()
        self.client.post(
            "/frustrations/1/edit",
            data={
                "title": "Manual reporting takes too long",
                "department": "Operations",
                "description": "The weekly report is assembled by hand from four systems.",
                "business_impact": "Managers wait longer for updates and errors slip through.",
                "frequency_value": "weekly",
                "pain_score": "8",
                "estimated_hours_lost": "3.5",
                "current_workaround": "One analyst merges everything in spreadsheets.",
                "ideal_outcome": "A single automated report with current numbers.",
                "status": "Resolved",
            },
            follow_redirects=True,
        )
        response = self.client.get("/")
        self.assertIn(b"Resolved", response.data)

    def test_delete_entry_removes_it(self) -> None:
        self.create_entry()
        self.client.post("/frustrations/1/delete", follow_redirects=True)
        response = self.client.get("/")
        self.assertNotIn(b"Manual reporting takes too long", response.data)


if __name__ == "__main__":
    unittest.main()
