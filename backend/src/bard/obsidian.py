"""Links into Obsidian.

Its URL handler only knows the vaults in its own registry (obsidian.json): `open?path=` fails for
a folder that isn't registered, and failed for the campaign vault here, which sits inside another
registered vault (backend/). Links name the vault by its registry id instead, which is exact.
"""

import json
import os
import re
from pathlib import Path

REGISTRY = Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
VAULT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def registry() -> Path:
    """BARD_OBSIDIAN_REGISTRY overrides it (tests)."""
    return Path(os.environ.get("BARD_OBSIDIAN_REGISTRY", REGISTRY))


def vault_for(folder: Path, path: Path | None = None) -> dict | None:
    """{"id": vault id, "folder": `folder` inside that vault ("" when it is the vault)} for the
    innermost registered vault holding `folder`; None when Obsidian doesn't know it."""
    try:
        vaults = json.loads((path or registry()).read_text()).get("vaults")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(vaults, dict):
        return None
    target = folder.resolve()
    best: tuple[str, Path] | None = None
    for vid, v in vaults.items():
        root = v.get("path") if isinstance(v, dict) else None
        if not isinstance(root, str) or not VAULT_ID.fullmatch(vid):
            continue
        root_path = Path(root).resolve()
        if (target == root_path or root_path in target.parents) and (best is None or len(root_path.parts) > len(best[1].parts)):
            best = (vid, root_path)
    if best is None:
        return None
    vid, root_path = best
    return {"id": vid, "folder": "" if target == root_path else target.relative_to(root_path).as_posix()}
