import json
import time
from pathlib import Path
from typing import Any, Dict

class AuditLogger:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def write_event(self, event: Dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("ts", time.time())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")
