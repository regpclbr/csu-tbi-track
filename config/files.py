"""Writing generated files (certificate PDFs, reports) into local storage (MEDIA_ROOT)."""

import os
import tempfile
from pathlib import Path

from django.core.files.storage import Storage


def write_file_atomically(storage: Storage, name: str, content: bytes) -> None:
    """Write to a temporary file in the same folder, then rename it into place: the final file is
    either complete or absent, never half-written."""
    target = Path(storage.path(name))
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".rendering-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
