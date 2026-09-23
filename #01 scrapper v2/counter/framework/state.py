import json
import sqlite3
from pathlib import Path
from .models import DocumentRecord, AuditEvent


class StateStore:
    """Durable deduplication and audit storage for one or many site adapters."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS records (
                fingerprint TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                category TEXT NOT NULL,
                record_date TEXT NOT NULL,
                title TEXT NOT NULL,
                detail_url TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL,
                event TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                document_url TEXT,
                file_path TEXT,
                file_sha256 TEXT,
                transport TEXT,
            message TEXT,
            metadata_json TEXT NOT NULL DEFAULT ''
        )
        """)
        self._add_column_if_missing("records", "metadata_json", "TEXT NOT NULL DEFAULT '{}'" )
        self._add_column_if_missing("audit_events", "metadata_json", "TEXT NOT NULL DEFAULT ''")
        self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_records_identity
            ON records(source, category, detail_url)
        """)
        self._db.commit()

    def _add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def seen(self, record: DocumentRecord) -> bool:
        return self._db.execute(
            """SELECT 1 FROM records
               WHERE source = ? AND category = ? AND detail_url = ?""",
            (record.source, record.category, record.detail_url),
        ).fetchone() is not None

    def upsert(self, record: DocumentRecord, timestamp: str) -> bool:
        is_new = not self.seen(record)
        existing = self._db.execute(
            """SELECT fingerprint FROM records
               WHERE source = ? AND category = ? AND detail_url = ?
               LIMIT 1""",
            (record.source, record.category, record.detail_url),
        ).fetchone()
        if existing:
            # The URL is SEBI's stable document identity. A changed visible
            # date/title is metadata churn, not a second document.
            self._db.execute(
                """UPDATE records
                   SET fingerprint = ?, record_date = ?, title = ?, last_seen_at = ?, metadata_json = ?
                   WHERE fingerprint = ?""",
                (record.fingerprint, record.record_date, record.title,
                 timestamp, json.dumps(dict(record.metadata), ensure_ascii=False), existing[0]),
            )
        else:
            self._db.execute(
                """INSERT INTO records
                   (fingerprint, source, category, record_date, title,
                    detail_url, first_seen_at, last_seen_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.fingerprint, record.source, record.category,
                 record.record_date, record.title, record.detail_url,
                 timestamp, timestamp,
                 json.dumps(dict(record.metadata), ensure_ascii=False)),
            )
        self._db.commit()
        return is_new

    def rekey_matching_record(self, record: DocumentRecord, timestamp: str) -> bool:
        """Migrate an older identity when a site exposes a better stable key."""
        existing = self._db.execute(
            """SELECT fingerprint FROM records
               WHERE source = ? AND category = ? AND title = ?
                 AND record_date = ? AND detail_url <> ?
               LIMIT 1""",
            (record.source, record.category, record.title, record.record_date,
             record.detail_url),
        ).fetchone()
        if not existing:
            return False
        self._db.execute(
            """UPDATE records
               SET fingerprint = ?, detail_url = ?, last_seen_at = ?, metadata_json = ?
               WHERE fingerprint = ?""",
            (record.fingerprint, record.detail_url, timestamp,
             json.dumps(dict(record.metadata), ensure_ascii=False), existing[0]),
        )
        self._db.commit()
        return True

    def audit(self, event: AuditEvent) -> None:
        self._db.execute("""
            INSERT INTO audit_events
            (fingerprint,event,occurred_at,document_url,file_path,file_sha256,transport,message,metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (event.fingerprint, event.event, event.occurred_at,
              event.document_url, event.file_path, event.file_sha256,
              event.transport, event.message, event.metadata_json))
        self._db.commit()

    def close(self) -> None:
        self._db.close()
