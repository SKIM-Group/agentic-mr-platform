from datetime import datetime

from pydantic import BaseModel, Field


class FieldSpec(BaseModel):
    name: str
    description: str
    prompt: str  # Question Claude asks if this field is missing


class SkillManifest(BaseModel):
    name: str
    display_name: str
    description: str
    version: str = "1.0"
    author: str = ""
    triggers: list[str] = Field(default_factory=list)
    required_fields: list[FieldSpec] = Field(default_factory=list)
    optional_fields: list[FieldSpec] = Field(default_factory=list)


class Skill(BaseModel):
    manifest: SkillManifest
    system_prompt: str  # Markdown body below frontmatter
    raw_content: str  # Full file content (frontmatter + body)
    file_path: str
    loaded_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def name(self) -> str:
        return self.manifest.name

    def to_summary(self) -> dict:
        return {
            "name": self.manifest.name,
            "display_name": self.manifest.display_name,
            "description": self.manifest.description,
            "triggers": self.manifest.triggers,
            "required_fields": [f.name for f in self.manifest.required_fields],
            "optional_fields": [f.name for f in self.manifest.optional_fields],
            "version": self.manifest.version,
            "author": self.manifest.author,
        }
