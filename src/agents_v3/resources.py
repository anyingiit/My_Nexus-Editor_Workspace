"""Locate immutable protocol assets in a source tree or installed wheel."""

from __future__ import annotations

from pathlib import Path
import sysconfig


class AssetError(FileNotFoundError):
    pass


def asset_root() -> Path:
    """Return the first complete, distribution-controlled asset root.

    Arbitrary environment-variable overrides are intentionally unsupported:
    redirecting a security schema at runtime would undermine the frozen
    contract.  Development trees are checked first; wheels install the same
    files under ``share/agents-v3``.
    """

    module = Path(__file__).resolve()
    # ``data_files`` land below the active prefix for a normal wheel install,
    # but ``pip --target`` places them below the target directory itself.  A
    # prefix install can likewise differ from the interpreter's sysconfig
    # prefix.  Derive those locations from the imported module rather than
    # trusting an environment override.
    candidates_list = [module.parents[2]]  # editable/source-tree layout
    candidates_list.append(module.parents[1] / "share" / "agents-v3")  # pip --target
    for ancestor in module.parents:
        if ancestor.name in {"site-packages", "dist-packages"} and len(ancestor.parents) >= 3:
            candidates_list.append(ancestor.parents[2] / "share" / "agents-v3")
    candidates_list.append(
        Path(sysconfig.get_path("data")).resolve() / "share" / "agents-v3"
    )
    candidates = tuple(dict.fromkeys(candidates_list))
    required = ("AGENTS.md", "config", "protocol", "schemas", "sandbox")
    for candidate in candidates:
        if all((candidate / item).exists() for item in required):
            return candidate
    rendered = ", ".join(str(path) for path in candidates)
    raise AssetError(f"AGENTS V3 assets are incomplete; checked: {rendered}")


def schema_path(name: str) -> Path:
    if not name or Path(name).name != name or not name.endswith(".schema.json"):
        raise AssetError(f"invalid schema name: {name!r}")
    path = asset_root() / "schemas" / name
    if not path.is_file():
        raise AssetError(f"published schema is missing: {path}")
    return path
