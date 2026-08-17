from sqlalchemy import Index, Integer, String, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Firmware(Base):
    __tablename__ = "firmwares"

    hardware: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    url: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String)
    timestamp: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    def to_json(self, archival: bool = False):
        result = {
            "url": self.url,
            "sha-256": self.sha256,
            "friendlyVersion": self.version,
            "timestamp": self.timestamp,
            "notes": self.notes if self.notes else self.version,
        }
        if archival:
            result["kind"] = self.kind
            result["hardware"] = self.hardware
        return result

    @classmethod
    def upsert(cls, session, hardware, kind, version, url, sha256, timestamp, notes):
        existing = session.scalars(
            select(cls).filter_by(hardware=hardware, kind=kind, version=version)
        ).one_or_none()
        if existing is None:
            session.add(
                cls(
                    hardware=hardware,
                    kind=kind,
                    version=version,
                    url=url,
                    sha256=sha256,
                    timestamp=timestamp,
                    notes=notes,
                )
            )
        else:
            existing.url = url
            existing.sha256 = sha256
            existing.timestamp = timestamp
            existing.notes = notes


Index(
    "ix_firmwares_hardware_kind_timestamp",
    Firmware.hardware,
    Firmware.kind,
    Firmware.timestamp.desc(),
)
