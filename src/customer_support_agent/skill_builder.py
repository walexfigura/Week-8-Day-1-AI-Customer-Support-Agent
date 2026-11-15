"""Build the Claude Skill ZIP from `claude-skill/customer-support/`.

The hand-written Skill files (SKILL.md and four reference guides) live in
`claude-skill/customer-support/`. Business policies, FAQs, procedures, and
workflows are NOT duplicated there by hand — this builder copies the
authoritative versions from `knowledge-base/` and `agent/workflows/` into the
package at build time, so the Python agent and the Skill can never disagree.

Output: dist/customer-support-skill.zip containing a single top-level folder
`customer-support/` with SKILL.md inside (the layout Claude expects).
"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .knowledge import parse_front_matter

SKILL_NAME = "customer-support"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_DESCRIPTION = 200  # claude.ai's limit is the strictest we target; the spec allows 1024
FIXED_ZIP_TIME = (2026, 11, 10, 0, 0, 0)  # reproducible archives


@dataclass
class BuildReport:
    zip_path: Path
    staging_dir: Path
    files: list[str]
    warnings: list[str]


class SkillValidationError(ValueError):
    pass


def validate_skill_md(path: Path, folder_name: str) -> tuple[list[str], list[str]]:
    """Raise on hard errors; return (soft warnings, reference paths mentioned in SKILL.md)."""
    if not path.exists():
        raise SkillValidationError(f"{path} not found")
    text = path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(text)
    if not meta:
        raise SkillValidationError("SKILL.md must start with YAML front matter between '---' lines")
    name, description = meta.get("name", ""), meta.get("description", "")
    if not NAME_RE.match(name) or len(name) > 64:
        raise SkillValidationError(f"Invalid name {name!r}: use lowercase letters, numbers and hyphens (max 64)")
    if "claude" in name or "anthropic" in name:
        raise SkillValidationError("Skill names may not contain 'claude' or 'anthropic'")
    if name != folder_name:
        raise SkillValidationError(f"Front matter name {name!r} must match the folder name {folder_name!r}")
    if not description:
        raise SkillValidationError("description is required")
    if len(description) > MAX_DESCRIPTION:
        raise SkillValidationError(f"description is {len(description)} chars; keep it <= {MAX_DESCRIPTION}")
    if re.search(r"<[^>]+>", description):
        raise SkillValidationError("description must not contain XML/HTML tags")
    warnings = []
    lines = body.count("\n")
    if lines > 500:
        warnings.append(f"SKILL.md body is {lines} lines; consider moving detail into references/")
    referenced = sorted(set(re.findall(r"(references/[\w\-/.]+\.(?:md|json))", body)))
    return warnings, referenced


def build_skill(project_root: Path, out_dir: Path | None = None) -> BuildReport:
    source = project_root / "claude-skill" / SKILL_NAME
    out_dir = out_dir or (project_root / "dist")
    staging_root = out_dir / "skill-build"
    staging = staging_root / SKILL_NAME

    warnings, referenced = validate_skill_md(source / "SKILL.md", source.name)

    if staging_root.exists():
        shutil.rmtree(staging_root)
    shutil.copytree(source, staging)

    refs = staging / "references"
    shutil.copytree(project_root / "knowledge-base", refs / "knowledge-base")
    shutil.copytree(project_root / "agent" / "workflows", refs / "workflows")
    (refs / "sample-data").mkdir(parents=True, exist_ok=True)
    for name in ("shopify-orders.json", "shopify-products.json", "zendesk-tickets.json"):
        shutil.copy2(project_root / "data" / name, refs / "sample-data" / name)
    (refs / "GENERATED.md").write_text(
        "# Generated files\n\n"
        "`knowledge-base/`, `workflows/`, and `sample-data/` were copied from the source repository by\n"
        "`python run.py build-skill`. Edit the originals there, not these copies.\n\n"
        "Everything in `sample-data/` is FICTIONAL demonstration data for Northstar Supply.\n",
        encoding="utf-8",
    )

    for ref in referenced:
        if not (staging / ref).exists():
            raise SkillValidationError(f"SKILL.md references {ref}, which is not in the built package")

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{SKILL_NAME}-skill.zip"
    files = sorted(p for p in staging.rglob("*") if p.is_file() and p.name != ".DS_Store")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(staging_root).as_posix(), FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return BuildReport(zip_path, staging, [p.relative_to(staging_root).as_posix() for p in files], warnings)


def verify_zip(zip_path: Path) -> list[str]:
    """Re-open the archive and confirm the layout Claude expects."""
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if f"{SKILL_NAME}/SKILL.md" not in names:
            raise SkillValidationError("ZIP must contain customer-support/SKILL.md")
        tops = {n.split("/", 1)[0] for n in names}
        if tops != {SKILL_NAME}:
            raise SkillValidationError(f"ZIP must have a single top-level folder, found {sorted(tops)}")
        meta, _ = parse_front_matter(archive.read(f"{SKILL_NAME}/SKILL.md").decode("utf-8"))
        if meta.get("name") != SKILL_NAME:
            raise SkillValidationError("SKILL.md inside the ZIP has the wrong name")
    return names
