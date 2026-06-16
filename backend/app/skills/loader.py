"""Load and parse skill markdown files."""

import logging
from pathlib import Path

import frontmatter

from app.skills.models import FieldSpec, Skill, SkillManifest

logger = logging.getLogger(__name__)


def load_skill_from_file(file_path: str) -> Skill | None:
    """Parse a .md file into a Skill object. Returns None on any error."""
    path = Path(file_path)
    if not path.exists() or path.suffix != ".md":
        return None

    try:
        raw_content = path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw_content)
        meta = post.metadata

        required_fields = [
            FieldSpec(
                name=f["name"],
                description=f.get("description", ""),
                prompt=f.get("prompt", f"Please provide {f['name']}."),
            )
            for f in meta.get("required_fields", [])
            if isinstance(f, dict) and "name" in f
        ]

        optional_fields = [
            FieldSpec(
                name=f["name"],
                description=f.get("description", ""),
                prompt=f.get("prompt", f"Optionally provide {f['name']}."),
            )
            for f in meta.get("optional_fields", [])
            if isinstance(f, dict) and "name" in f
        ]

        manifest = SkillManifest(
            name=meta.get("name", path.stem),
            display_name=meta.get("display_name", path.stem.replace("_", " ").title()),
            description=meta.get("description", ""),
            version=str(meta.get("version", "1.0")),
            author=str(meta.get("author", "")),
            triggers=list(meta.get("triggers", [])),
            required_fields=required_fields,
            optional_fields=optional_fields,
        )

        return Skill(
            manifest=manifest,
            system_prompt=post.content.strip(),
            raw_content=raw_content,
            file_path=str(path.resolve()),
        )

    except Exception as exc:
        logger.warning("Failed to load skill from %s: %s", file_path, exc)
        return None


def load_all_skills(skills_dir: str) -> dict[str, Skill]:
    """Load all .md skill files from a directory."""
    directory = Path(skills_dir)
    if not directory.exists():
        logger.warning("Skills directory not found: %s", skills_dir)
        return {}

    skills: dict[str, Skill] = {}
    for md_file in directory.glob("*.md"):
        skill = load_skill_from_file(str(md_file))
        if skill:
            skills[skill.name] = skill
            logger.info("Loaded skill: %s", skill.name)

    return skills
