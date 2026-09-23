from dataclasses import dataclass, field
from hashlib import sha256


@dataclass(frozen=True)
class DocumentRecord:
    source: str
    category: str
    record_date: str
    title: str
    detail_url: str
    document_urls: tuple[str, ...] = field(default_factory=tuple)
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def fingerprint(self) -> str:
        value = "|".join((self.source, self.category, self.record_date,
                          self.title, self.detail_url))
        return sha256(value.encode("utf-8")).hexdigest()


@dataclass
class AuditEvent:
    fingerprint: str
    source: str
    category: str
    record_date: str
    title: str
    detail_url: str
    event: str
    occurred_at: str
    document_url: str = ""
    file_path: str = ""
    file_sha256: str = ""
    transport: str = ""
    message: str = ""
    metadata_json: str = ""
