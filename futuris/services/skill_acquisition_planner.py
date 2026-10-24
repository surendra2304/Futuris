"""
Futuris/services/skill_acquisition_planner.py — Futuris Skill Acquisition Planner.

Skill gap analysis, learning path generation, resource recommendation,
and progress tracking for skill development.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class SkillRequirement:
    """A skill requirement."""
    skill_name: str
    importance: float = 0.5  # 0.0 to 1.0
    current_level: float = 0.0  # 0.0 to 1.0
    target_level: float = 0.8
    category: str = "technical"  # technical, soft, domain, meta


@dataclass
class LearningResource:
    """A learning resource recommendation."""
    resource_id: str
    skill_name: str
    resource_type: str = "course"  # course, book, video, article, project
    title: str = ""
    estimated_hours: float = 0.0
    difficulty: str = "beginner"  # beginner, intermediate, advanced
    cost: float = 0.0
    url: str = ""


@dataclass
class LearningPath:
    """A personalized learning path."""
    path_id: str
    goal: str
    skills: list[SkillRequirement] = field(default_factory=list)
    resources: list[LearningResource] = field(default_factory=list)
    estimated_total_hours: float = 0.0
    progress_pct: float = 0.0
    created_at: float = field(default_factory=time.time)


class SkillAcquisitionPlanner:
    """Engine for skill acquisition planning."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillRequirement] = {}
        self._paths: list[LearningPath] = []
        self._resources: dict[str, LearningResource] = {}

    def add_skill(self, skill_name: str, importance: float = 0.5,
                  current_level: float = 0.0, target_level: float = 0.8,
                  category: str = "technical") -> str:
        """Add a skill requirement."""
        skill = SkillRequirement(
            skill_name=skill_name, importance=importance,
            current_level=current_level, target_level=target_level,
            category=category,
        )
        self._skills[skill_name] = skill
        return skill_name

    def recommend_resources(self, skill_name: str) -> list[LearningResource]:
        """Recommend learning resources for a skill."""
        skill = self._skills.get(skill_name)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")

        resources = []
        if skill.target_level > skill.current_level:
            diff = skill.target_level - skill.current_level
            resources.append(LearningResource(
                resource_id=f"res_{uuid.uuid4().hex[:8]}",
                skill_name=skill_name,
                resource_type="course",
                title=f"{skill_name} Fundamentals",
                estimated_hours=diff * 20,
                difficulty="beginner" if diff < 0.5 else "intermediate",
                cost=0.0,
            ))
            resources.append(LearningResource(
                resource_id=f"res_{uuid.uuid4().hex[:8]}",
                skill_name=skill_name,
                resource_type="project",
                title=f"Hands-on {skill_name} Project",
                estimated_hours=diff * 15,
                difficulty="intermediate",
                cost=0.0,
            ))
        for res in resources:
            self._resources[res.resource_id] = res
        return resources

    def create_learning_path(self, goal: str, skill_names: list[str]) -> LearningPath:
        """Create a personalized learning path."""
        path_id = f"path_{uuid.uuid4().hex[:12]}"
        path = LearningPath(path_id=path_id, goal=goal)

        for name in skill_names:
            skill = self._skills.get(name)
            if skill:
                path.skills.append(skill)
                resources = self.recommend_resources(name)
                path.resources.extend(resources)

        path.estimated_total_hours = sum(r.estimated_hours for r in path.resources)
        total_progress = sum(
            min(1.0, s.current_level / s.target_level) if s.target_level > 0 else 0
            for s in path.skills
        ) / max(1, len(path.skills))
        path.progress_pct = total_progress * 100
        self._paths.append(path)
        return path

    def get_learning_path(self, path_id: str) -> dict[str, Any]:
        """Get a learning path."""
        for path in self._paths:
            if path.path_id == path_id:
                return {
                    "path_id": path.path_id,
                    "goal": path.goal,
                    "skill_count": len(path.skills),
                    "resource_count": len(path.resources),
                    "estimated_total_hours": path.estimated_total_hours,
                    "progress_pct": path.progress_pct,
                }
        raise HTTPException(status_code=404, detail="Learning path not found")

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_skills": len(self._skills),
            "total_paths": len(self._paths),
            "total_resources": len(self._resources),
            "engine_status": "operational",
        }


engine = SkillAcquisitionPlanner()


@router.post("/futuris/skills/add")
async def add_skill(skill_name: str, importance: float = 0.5, current_level: float = 0.0,
                    target_level: float = 0.8, category: str = "technical"):
    """Add a skill requirement."""
    engine.add_skill(skill_name, importance, current_level, target_level, category)
    return {"skill_name": skill_name, "status": "added"}


@router.post("/futuris/skills/resources/{skill_name}")
async def recommend_resources(skill_name: str):
    """Recommend learning resources for a skill."""
    resources = engine.recommend_resources(skill_name)
    return {
        "skill_name": skill_name,
        "resource_count": len(resources),
        "resources": [
            {"title": r.title, "estimated_hours": r.estimated_hours, "type": r.resource_type}
            for r in resources
        ],
    }


@router.post("/futuris/skills/path")
async def create_learning_path(goal: str, skill_names: list[str]):
    """Create a learning path."""
    path = engine.create_learning_path(goal, skill_names)
    return {
        "path_id": path.path_id,
        "estimated_total_hours": path.estimated_total_hours,
        "progress_pct": path.progress_pct,
    }


@router.get("/futuris/skills/path/{path_id}")
async def get_learning_path(path_id: str):
    """Get a learning path."""
    return engine.get_learning_path(path_id)


@router.get("/futuris/skills/report")
async def skill_report():
    """Get skill acquisition report."""
    return engine.get_report()
