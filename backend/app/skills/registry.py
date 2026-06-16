"""Thread-safe skill registry with filesystem hot-reload via watchdog."""

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver as Observer

from app.skills.loader import load_all_skills, load_skill_from_file
from app.skills.models import Skill

logger = logging.getLogger(__name__)


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._lock = threading.Lock()
        self._path_to_name: dict[str, str] = {}  # file_path → skill_name

    def load_from_dir(self, skills_dir: str) -> None:
        skills = load_all_skills(skills_dir)
        with self._lock:
            self._skills = skills
            self._path_to_name = {s.file_path: s.name for s in skills.values()}

    def get_all(self) -> list[Skill]:
        with self._lock:
            return list(self._skills.values())

    def get_by_name(self, name: str) -> Skill | None:
        with self._lock:
            return self._skills.get(name)

    def reload_file(self, file_path: str) -> None:
        skill = load_skill_from_file(file_path)
        with self._lock:
            # Remove old entry if name changed
            old_name = self._path_to_name.get(file_path)
            if old_name and old_name in self._skills:
                del self._skills[old_name]
            if skill:
                self._skills[skill.name] = skill
                self._path_to_name[file_path] = skill.name
                logger.info("Reloaded skill: %s", skill.name)

    def remove_file(self, file_path: str) -> None:
        with self._lock:
            name = self._path_to_name.pop(file_path, None)
            if name and name in self._skills:
                del self._skills[name]
                logger.info("Removed skill: %s", name)

    def save_skill(self, name: str, content: str, skills_dir: str) -> Skill | None:
        """Write content to file and reload."""
        file_path = str(Path(skills_dir) / f"{name}.md")
        Path(file_path).write_text(content, encoding="utf-8")
        self.reload_file(file_path)
        return self.get_by_name(name)

    def delete_skill(self, name: str) -> bool:
        skill = self.get_by_name(name)
        if not skill:
            return False
        path = Path(skill.file_path)
        if path.exists():
            path.unlink()
        self.remove_file(skill.file_path)
        return True

    def skills_summary(self) -> str:
        """Compact description of all skills for pipeline routing."""
        skills = self.get_all()
        if not skills:
            return "No skills available."
        lines = []
        for s in skills:
            triggers = ", ".join(s.manifest.triggers) if s.manifest.triggers else "general"
            lines.append(
                f"- {s.name}: {s.manifest.description} (triggers: {triggers})"
            )
        return "\n".join(lines)


class _SkillFileHandler(FileSystemEventHandler):
    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and str(event.src_path).endswith(".md"):
            self._registry.reload_file(str(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and str(event.src_path).endswith(".md"):
            self._registry.reload_file(str(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and str(event.src_path).endswith(".md"):
            self._registry.remove_file(str(event.src_path))


class SkillWatcher:
    def __init__(self) -> None:
        self._observer: Observer | None = None

    def start(self, skills_dir: str, registry: SkillRegistry) -> None:
        self._observer = Observer()
        self._observer.schedule(
            _SkillFileHandler(registry), path=skills_dir, recursive=False
        )
        self._observer.start()
        logger.info("Watching skills directory: %s", skills_dir)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()


registry = SkillRegistry()
watcher = SkillWatcher()
