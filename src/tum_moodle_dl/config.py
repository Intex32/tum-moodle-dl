"""Where things live, and the config file listing the courses to sync."""

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit
from tomlkit.exceptions import ParseError
from tomlkit.items import AoT, Table

APP = "tum-moodle-dl"
DEFAULT_ROOT = Path("~/Moodle")


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / APP


def default_config_path() -> Path:
    return config_dir() / "config.toml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class CourseConfig:
    id: int
    dir: Path | None = None
    original_filenames: bool | None = None


@dataclass(frozen=True)
class Config:
    root: Path = DEFAULT_ROOT
    original_filenames: bool = False
    courses: list[CourseConfig] = field(default_factory=list)

    def course(self, course_id: int) -> CourseConfig:
        return next((c for c in self.courses if c.id == course_id), CourseConfig(course_id))

    def destination(self, course: CourseConfig, shortname: str) -> Path:
        """The course's folder: ``dir`` if set (relative to ``root``), else its short name."""
        folder = course.dir or Path(_folder_name(shortname) or str(course.id))
        return (self.root.expanduser() / folder.expanduser()).resolve()

    def uses_original_filenames(self, course: CourseConfig) -> bool:
        if course.original_filenames is None:
            return self.original_filenames
        return course.original_filenames


def load(path: Path) -> Config:
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    try:
        courses = [_course(c) for c in data.get("courses", [])]
        return Config(
            root=Path(_typed(data, "root", str, str(DEFAULT_ROOT))),
            original_filenames=_typed(data, "original_filenames", bool, False),
            courses=courses,
        )
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from None


def _course(data: dict) -> CourseConfig:
    if not isinstance(data, dict) or not isinstance(data.get("id"), int):
        raise ConfigError("every [[courses]] entry needs a numeric id")
    folder = _typed(data, "dir", str, None)
    return CourseConfig(
        id=data["id"],
        dir=Path(folder) if folder else None,
        original_filenames=_typed(data, "original_filenames", bool, None),
    )


def _typed(data: dict, key: str, kind: type, default):
    value = data.get(key, default)
    if value is not None and not isinstance(value, kind):
        raise ConfigError(f"{key} must be a {kind.__name__}")
    return value


def _folder_name(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|\x00-\x1f]+', "-", name).strip(" .-")


class ConfigEditor:
    """Changes the course list in the config file, keeping your comments and formatting."""

    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            try:
                self.doc = tomlkit.parse(path.read_text())
            except ParseError as e:
                raise ConfigError(f"{path}: {e}") from None
        else:
            self.doc = _new_document()
        courses = self.doc.get("courses")
        if courses is not None and not isinstance(courses, AoT):
            raise ConfigError(f"{path}: write courses as [[courses]] tables to edit them with tmdl")
        # tomlkit separates new tables with a blank line, but not ones added to a list it read.
        self._courses_from_file = courses is not None

    def add(
        self,
        course_id: int,
        name: str | None = None,
        *,
        folder: str | None = None,
        original_filenames: bool | None = None,
    ) -> bool:
        """Add a course, or update the given settings of one already there. True if new."""
        table = self._find(course_id)
        if table is None:
            table = tomlkit.table()
            table.add("id", tomlkit.item(course_id).comment(name) if name else course_id)
            courses = self.doc.get("courses")
            if courses is None:
                courses = tomlkit.aot()
                self.doc.append("courses", courses)
            elif self._courses_from_file and not courses[-1].as_string().endswith("\n\n"):
                courses[-1].add(tomlkit.nl())
            courses.append(table)
            new = True
        else:
            new = False
        if folder is not None:
            table["dir"] = folder
        if original_filenames is not None:
            table["original_filenames"] = original_filenames
        return new

    def remove(self, course_id: int) -> bool:
        courses = self.doc.get("courses")
        if courses is None or self._find(course_id) is None:
            return False
        for i in reversed(range(len(courses))):
            if courses[i].get("id") == course_id:
                del courses[i]
        return True

    def save(self) -> None:
        text = tomlkit.dumps(self.doc)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(text)
        try:
            load(tmp)  # never save a config tmdl can't read back
        except ConfigError:
            tmp.unlink()
            raise
        tmp.replace(self.path)

    def _find(self, course_id: int) -> Table | None:
        courses = self.doc.get("courses") or []
        return next((t for t in courses if t.get("id") == course_id), None)


def _new_document() -> tomlkit.TOMLDocument:
    doc = tomlkit.document()
    doc.add(
        tomlkit.comment("tum-moodle-dl settings. Manage courses with `tmdl courses add/remove`.")
    )
    doc.add(tomlkit.comment('root = "~/Moodle"            # where course folders go'))
    doc.add(tomlkit.comment("original_filenames = false   # name files as uploaded"))
    doc.add(tomlkit.nl())
    return doc
