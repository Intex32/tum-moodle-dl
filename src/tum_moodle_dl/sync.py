"""Bring a course folder up to date from a Download Center zip without losing local changes.

Rules:
- files that are new on Moodle are added;
- files that changed on Moodle are replaced, but only if you have not edited them since we
  last wrote them; otherwise Moodle's version is saved next to yours as "name (moodle).ext";
- nothing is ever deleted.

To know whether you edited a file, the folder keeps a manifest of what we last wrote.
"""

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MANIFEST = ".tmdl-manifest.json"
UTF8_FLAG = 0x800
_UMASK = os.umask(0)
os.umask(_UMASK)


@dataclass
class Conflict:
    local: Path
    remote_copy: Path


@dataclass
class SyncReport:
    added: list[Path] = field(default_factory=list)
    updated: list[Path] = field(default_factory=list)
    unchanged: int = 0
    conflicts: list[Conflict] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{len(self.added)} new", f"{len(self.updated)} updated"]
        if self.conflicts:
            parts.append(f"{len(self.conflicts)} kept with your edits")
        parts.append(f"{self.unchanged} unchanged")
        return ", ".join(parts)


def sync_zip(archive: Path, dest: Path) -> SyncReport:
    dest.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(dest)
    report = SyncReport()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = _safe_path(_entry_name(info))
            target = dest / rel
            key = rel.as_posix()
            remote = _hash_member(zf, info)
            if not target.exists():
                _extract(zf, info, target)
                report.added.append(target)
            else:
                local = _hash_file(target)
                if local == remote:
                    report.unchanged += 1
                elif manifest.get(key) == local:
                    _extract(zf, info, target)
                    report.updated.append(target)
                else:
                    copy = target.with_name(f"{target.stem} (moodle){target.suffix}")
                    if not copy.exists() or _hash_file(copy) != remote:
                        _extract(zf, info, copy)
                    report.conflicts.append(Conflict(target, copy))
                    continue  # the file there is yours, not ours
            manifest[key] = remote
    _save_manifest(dest, manifest)
    return report


def _entry_name(info: zipfile.ZipInfo) -> str:
    """Zip tools that don't mark UTF-8 names get them decoded as cp437; undo that."""
    if info.flag_bits & UTF8_FLAG:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except UnicodeError:
        return info.filename


def _safe_path(name: str) -> Path:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"refusing to extract {name!r}: it points outside the course folder")
    return Path(*path.parts)


def _hash_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with zf.open(info) as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _extract(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    """Write atomically, so an interrupted sync never leaves half a file behind."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmdl-")
    try:
        with os.fdopen(fd, "wb") as out, zf.open(info) as src:
            shutil.copyfileobj(src, out)
        os.chmod(tmp, 0o666 & ~_UMASK)  # mkstemp makes files private; use normal permissions
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load_manifest(dest: Path) -> dict[str, str]:
    try:
        return json.loads((dest / MANIFEST).read_text())["files"]
    except (FileNotFoundError, ValueError, KeyError):
        return {}


def _save_manifest(dest: Path, manifest: dict[str, str]) -> None:
    (dest / MANIFEST).write_text(json.dumps({"files": manifest}, indent=1, sort_keys=True))
