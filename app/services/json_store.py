from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            records = self._read()
            return records[-limit:][::-1]

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next(
                (record for record in self._read() if record["id"] == record_id),
                None,
            )

    def add(self, payload: Any) -> dict[str, Any]:
        record = {
            "id": uuid.uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }
        with self._lock:
            records = self._read()
            records.append(record)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary_path.write_text(
                json.dumps(records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        return record

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read JSON data store: {exc}") from exc
        if not isinstance(records, list):
            raise RuntimeError("JSON data store must contain an array of records")
        return records


data_store = JsonStore(Path(__file__).resolve().parents[1] / "data" / "posted_data.json")