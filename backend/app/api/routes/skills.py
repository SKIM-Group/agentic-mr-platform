"""Skill management CRUD endpoints."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.deps import verify_api_key
from app.core.config import settings
from app.skills.registry import registry

logger = logging.getLogger(__name__)
router = APIRouter()


class SkillUpsertRequest(BaseModel):
    name: str
    content: str  # Full raw markdown (frontmatter + body)


@router.get("")
async def list_skills() -> dict:
    skills = registry.get_all()
    return {
        "skills": [s.to_summary() for s in skills],
        "total": len(skills),
    }


@router.get("/{name}")
async def get_skill(name: str) -> dict:
    skill = registry.get_by_name(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return {
        **skill.to_summary(),
        "system_prompt": skill.system_prompt,
        "raw_content": skill.raw_content,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_skill(
    req: SkillUpsertRequest,
    _: None = Depends(verify_api_key),
) -> dict:
    existing = registry.get_by_name(req.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Skill '{req.name}' already exists. Use PUT to update.",
        )
    skill = registry.save_skill(req.name, req.content, settings.skills_dir)
    if not skill:
        raise HTTPException(status_code=422, detail="Failed to parse skill file. Check YAML frontmatter.")
    return skill.to_summary()


@router.put("/{name}")
async def update_skill(
    name: str,
    req: SkillUpsertRequest,
    _: None = Depends(verify_api_key),
) -> dict:
    # Allow name in URL to differ from name in content (content wins)
    skill = registry.save_skill(req.name or name, req.content, settings.skills_dir)
    if not skill:
        raise HTTPException(status_code=422, detail="Failed to parse skill file.")
    return skill.to_summary()


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    name: str,
    _: None = Depends(verify_api_key),
) -> None:
    deleted = registry.delete_skill(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_skill_file(
    file: UploadFile,
    _: None = Depends(verify_api_key),
) -> dict:
    """Upload a .md skill file directly."""
    if not file.filename or not file.filename.endswith(".md"):
        raise HTTPException(status_code=422, detail="Only .md files are accepted.")

    content_bytes = await file.read()
    content = content_bytes.decode("utf-8")
    name = Path(file.filename).stem

    skill = registry.save_skill(name, content, settings.skills_dir)
    if not skill:
        raise HTTPException(status_code=422, detail="Failed to parse skill file.")
    return skill.to_summary()
