"""
Back up the SQLite database into backups/ before making bulk changes.

    python manage.py backup_db before-import     # -> backups/2026-11-02_1530_before-import.sqlite3

Uses SQLite's online backup, so it's safe while the server is running.
"""
import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.text import slugify


class Command(BaseCommand):
    help = "Copy the SQLite database to backups/<date>_<label>.sqlite3."

    def add_arguments(self, parser):
        parser.add_argument("label", nargs="?", default="manual", help="Short note, e.g. before-import")

    def handle(self, label, **options):
        db = settings.DATABASES["default"]
        if not db["ENGINE"].endswith("sqlite3"):
            raise CommandError("backup_db only handles the SQLite database.")
        folder = Path(settings.BASE_DIR) / "backups"
        folder.mkdir(exist_ok=True)
        target = folder / f"{timezone.localtime():%Y-%m-%d_%H%M}_{slugify(label) or 'manual'}.sqlite3"
        with sqlite3.connect(db["NAME"]) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
        self.stdout.write(self.style.SUCCESS(f"Backed up to {target.relative_to(settings.BASE_DIR)}"))
