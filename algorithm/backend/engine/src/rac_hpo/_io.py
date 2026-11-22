"""Small shared atomic writer; independent of storage schemas and UI."""
import os
import uuid
import time
from pathlib import Path


def atomic_text(path: str | Path, content: str, *, overwrite: bool = True) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            for attempt in range(6):
                try:
                    os.replace(temporary, target)
                    break
                except PermissionError as error:
                    if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 5:
                        raise
                    time.sleep(min(0.025 * 2 ** attempt, 0.2))
        else:
            os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
