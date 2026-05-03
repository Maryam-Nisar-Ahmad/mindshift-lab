"""Resolve segment stills from numbered and Grok-download naming schemes."""

from __future__ import annotations

import re
from pathlib import Path

# Prefer JFIF first for Grok downloads, then common image formats.
SEGMENT_IMAGE_EXTENSIONS = (".jfif", ".png", ".jpg", ".jpeg")

# Canonical (recommended): download (1).jfif, download (2).jfif, … (number = scene).
# Legacy browser order: first save is bare download.jfif (scene 1), then download (1).jfif (scene 2), …
_GROK_DOWNLOAD_RE = re.compile(
    r"^download(?:\s*\((\d+)\))?\s*(\.(?:jfif|jpe?g|png))$",
    re.IGNORECASE,
)


def grok_stem_for_segment_id(scene_id: int) -> str:
    """Filename stem (no extension): scene N → 'download (N)'."""
    if scene_id < 1:
        raise ValueError("scene_id must be >= 1")
    return f"download ({scene_id})"


def grok_download_legacy_first_file(images_dir: Path) -> bool:
    """True if images/ contains a bare 'download.ext' (browser's first save) — legacy scene-1 naming."""
    d = Path(images_dir)
    if not d.is_dir():
        return False
    for p in d.iterdir():
        if not p.is_file():
            continue
        m = _GROK_DOWNLOAD_RE.match(p.name)
        if m and m.group(1) is None:
            return True
    return False


def scene_id_from_grok_download_basename(name: str, *, legacy_first_download: bool) -> int | None:
    """
    If name matches Grok download pattern, return 1-based scene id; else None.

    legacy_first_download: bare download.* → scene 1; download (k).* → scene k+1.
    Otherwise (canonical): download (k).* → scene k; bare download.* → 1 if present.
    """
    m = _GROK_DOWNLOAD_RE.match(name)
    if not m:
        return None
    inner = m.group(1)
    if legacy_first_download:
        if inner is None:
            return 1
        return int(inner) + 1
    if inner is None:
        return 1
    return int(inner)


def _grok_files_for_scene_id(images_dir: Path, scene_id: int, *, legacy_first_download: bool) -> list[Path]:
    d = Path(images_dir)
    return [
        p
        for p in d.iterdir()
        if p.is_file()
        and scene_id_from_grok_download_basename(p.name, legacy_first_download=legacy_first_download) == scene_id
    ]


def insert_grok_download_slot(images_dir: Path, segment_id: int) -> list[dict[str, str]]:
    """
    Make an empty filename slot for this scene by renaming Grok download files
    at this scene and later: scene N -> N+1, from the highest id down (so nothing is overwritten).

    Only renames files that match the Grok download name pattern. Numbered stills (1.png) are left alone.
    """
    if segment_id < 1:
        raise ValueError("segment_id must be >= 1")
    d = Path(images_dir)
    if not d.is_dir():
        return []

    legacy = grok_download_legacy_first_file(d)
    scene_ids: set[int] = set()
    for p in d.iterdir():
        if not p.is_file():
            continue
        sid = scene_id_from_grok_download_basename(p.name, legacy_first_download=legacy)
        if sid is not None:
            scene_ids.add(sid)
    if not scene_ids:
        return []

    max_s = max(scene_ids)
    if segment_id > max_s:
        return []

    renamed: list[dict[str, str]] = []
    for t in range(max_s, segment_id - 1, -1):
        for path in _grok_files_for_scene_id(d, t, legacy_first_download=legacy):
            new_name = grok_stem_for_segment_id(t + 1) + path.suffix.lower()
            new_path = d / new_name
            if new_path.exists() and new_path.resolve() != path.resolve():
                raise OSError(
                    f"refusing to overwrite {new_name!r}; unexpected existing file (try manual cleanup)"
                )
            old_name = path.name
            path.rename(new_path)
            renamed.append({"from": old_name, "to": new_name})
    return renamed


def resolve_segment_image_basename(images_dir: Path | str, segment_id: int) -> str | None:
    """Return basename for segment using numbered or Grok download names."""
    d = Path(images_dir)
    try:
        sid = int(segment_id)
    except (TypeError, ValueError):
        return None
    legacy = grok_download_legacy_first_file(d)
    stems: list[str] = [str(sid)]
    if legacy:
        if sid == 1:
            stems.extend(("download", grok_stem_for_segment_id(sid)))
        elif sid >= 2:
            stems.extend((f"download ({sid - 1})", grok_stem_for_segment_id(sid)))
    else:
        stems.append(grok_stem_for_segment_id(sid))
    for stem in stems:
        for ext in SEGMENT_IMAGE_EXTENSIONS:
            name = stem + ext
            if (d / name).is_file():
                return name
    return None


def resolve_image_path_under_dir(images_dir: Path | str, segment_id: int, image_field: str | None) -> Path | None:
    """
    Pick an existing image file for a segment:
    1) literal filename from segments.json if it exists
    2) same stem + jfif/png/jpg/jpeg
    3) segment_id-based fallback (numbered and download naming)
    """
    d = Path(images_dir)
    stem_from_field = ""
    if image_field:
        base = Path(image_field).name
        p = d / base
        if p.is_file():
            return p
        stem_from_field = Path(image_field).stem
        for ext in SEGMENT_IMAGE_EXTENSIONS:
            c = d / (stem_from_field + ext)
            if c.is_file():
                return c
    bn = resolve_segment_image_basename(d, segment_id)
    if bn:
        return d / bn
    if stem_from_field:
        for ext in SEGMENT_IMAGE_EXTENSIONS:
            c = d / (stem_from_field + ext)
            if c.is_file():
                return c
    return None
