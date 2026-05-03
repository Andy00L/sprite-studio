"""Static asset server for the sprite-studio web canvas.

Serves PNG/MP4 files under projects/<project_id>/{cast,shots,output,audio}/
read-only with CORS allow for the Vite dev origin.

Also accepts authenticated POSTs to /<project_id>/refs/upload, which save
user-uploaded reference images under projects/<project_id>/refs/<ulid>.<ext>
for downstream use by the cast designer + sprite-sheet generator. The upload
endpoint requires a Bearer token (the same API_SERVER_KEY the bridge uses)
so the local dev box doesn't accept anonymous writes from a browser tab.

NOT for production: bound to 127.0.0.1, single-user assumptions.
"""
from __future__ import annotations

import argparse
import logging
import mimetypes
import os
import re
import urllib.parse
from pathlib import Path
from typing import Optional

from aiohttp import web
from PIL import Image, UnidentifiedImageError
from ulid import ULID

logger = logging.getLogger("sprite_studio.asset_server")

# Top-level dirs under projects/<id>/ that callers may request. Anything
# else (e.g. _trash, _debug) is rejected so the asset server only ever
# leaks artifacts that belong to the user-visible canvas.
ALLOWED_SUBDIRS = {"cast", "shots", "output", "audio"}
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mp3", ".wav"}

DEFAULT_CORS_ORIGIN = "http://localhost:5173"

# Upload guards. 5 MB / 8192 px caps preempt PIL decompression bombs and
# keep a stray paste of an 80 MP photograph from filling disk.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_DIMENSION = 8192
ALLOWED_UPLOAD_MIMES = {"image/png", "image/jpeg", "image/webp"}
EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
PIL_FORMAT_TO_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}

# Crockford base32 ULID alphabet — no I/L/O/U.
ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def _projects_root() -> Path:
    return Path(__file__).resolve().parent.parent / "projects"


def _cors_origin() -> str:
    return os.environ.get("SPRITE_STUDIO_ASSET_CORS_ORIGIN", DEFAULT_CORS_ORIGIN)


@web.middleware
async def cors_middleware(request: web.Request, handler):
    origin = _cors_origin()
    if request.method == "OPTIONS":
        # Generic preflight for any path. Upload preflight uses a more
        # specific allow-headers list set by upload_options_handler.
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": "86400",
            },
        )
    response = await handler(request)
    response.headers.setdefault("Access-Control-Allow-Origin", origin)
    if request.method == "GET":
        response.headers.setdefault("Cache-Control", "private, max-age=60")
    return response


async def serve_asset(request: web.Request) -> web.StreamResponse:
    """GET /<project_id>/<subdir>/<rest...>"""
    project_id = request.match_info["project_id"]
    subdir = request.match_info["subdir"]
    rest = request.match_info["rest"]

    if subdir not in ALLOWED_SUBDIRS:
        raise web.HTTPNotFound(reason=f"subdir {subdir!r} not allowed")

    root = _projects_root().resolve()
    target = (root / project_id / subdir / rest).resolve()

    # The relative_to check rejects any path that escapes the projects
    # root via .. or symlink chicanery.
    try:
        target.relative_to(root)
    except ValueError:
        raise web.HTTPForbidden(reason="path traversal blocked")

    if not target.is_file():
        raise web.HTTPNotFound(reason="file not found")

    if target.suffix.lower() not in ALLOWED_EXTS:
        raise web.HTTPForbidden(reason=f"extension {target.suffix} not served")

    content_type, _ = mimetypes.guess_type(str(target))

    response_headers: dict[str, str] = {
        "Content-Type": content_type or "application/octet-stream",
    }
    if request.query.get("download") in ("1", "true", "yes"):
        # Sanitize: strip CR/LF and anything outside [A-Za-z0-9._-] so a
        # crafted ?name= can't inject a header line into the response.
        # Empty result falls back to a generic name.
        raw_name = request.query.get("name") or target.name
        safe_ascii = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("_") or "download"
        safe_utf8 = urllib.parse.quote(raw_name, safe="")
        response_headers["Content-Disposition"] = (
            f'attachment; filename="{safe_ascii}"; '
            f"filename*=UTF-8''{safe_utf8}"
        )

    return web.FileResponse(target, headers=response_headers)


async def health(request: web.Request) -> web.Response:
    root = _projects_root()
    return web.json_response({
        "status": "ok",
        "projects_root": str(root),
        "exists": root.exists(),
    })


def _err(status: int, msg: str, code: str) -> web.Response:
    return web.json_response({"error": msg, "code": code}, status=status)


def _check_auth(request: web.Request) -> Optional[web.Response]:
    api_key = request.app.get("api_key") or ""
    if not api_key:
        return _err(401, "upload endpoint disabled (no api_key configured)", "unauthorized")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return _err(401, "missing or malformed Authorization header", "unauthorized")
    if auth[len("Bearer "):] != api_key:
        return _err(401, "invalid api key", "unauthorized")
    return None


async def upload_options_handler(request: web.Request) -> web.Response:
    """CORS preflight for the upload endpoint.

    The browser sends OPTIONS before any POST that carries an
    Authorization header (it's not a CORS-safelisted header). We return
    the upload-specific allow list rather than relying on the generic
    middleware preflight so the response can advertise exactly the
    methods + headers this endpoint accepts.
    """
    return web.Response(
        status=204,
        headers={
            "Access-Control-Allow-Origin": _cors_origin(),
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Max-Age": "3600",
        },
    )


async def upload_reference_handler(request: web.Request) -> web.Response:
    """POST /<project_id>/refs/upload  (multipart/form-data, field name 'file').

    Streams the uploaded file to a tempfile while enforcing MAX_UPLOAD_BYTES,
    then validates with PIL (magic-byte mime + dimensions), then atomically
    renames into refs/<ulid>.<ext>. On any failure the tempfile is removed
    so refs/ never accumulates half-uploaded garbage.
    """
    if (err := _check_auth(request)) is not None:
        return err

    project_id = request.match_info["project_id"]
    if not ULID_RE.match(project_id):
        return _err(400, "invalid project_id format", "no_project")

    projects_root: Path = request.app["projects_root"]
    project_dir = projects_root / project_id
    if not project_dir.is_dir():
        return _err(404, "project not found", "no_project")

    refs_dir = project_dir / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)

    try:
        reader = await request.multipart()
    except Exception:
        return _err(400, "invalid multipart payload", "missing_field")

    field = await reader.next()
    if field is None or field.name != "file":
        return _err(400, "expected multipart field 'file'", "missing_field")

    declared_mime = (field.headers.get("Content-Type") or "").split(";")[0].strip()
    if declared_mime not in ALLOWED_UPLOAD_MIMES:
        return _err(400, f"unsupported mime: {declared_mime!r}", "invalid_mime")

    new_id = str(ULID())
    ext = EXT_BY_MIME[declared_mime]
    target = refs_dir / f"{new_id}.{ext}"
    tmp = refs_dir / f".{new_id}.{ext}.tmp"

    size = 0
    too_large = False
    try:
        try:
            with open(tmp, "wb") as f:
                while True:
                    chunk = await field.read_chunk(size=65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        too_large = True
                        break
                    f.write(chunk)
        except OSError as e:
            return _err(500, f"disk write failed: {e}", "internal")

        if too_large:
            return _err(
                413,
                f"upload exceeds {MAX_UPLOAD_BYTES} bytes",
                "too_large",
            )

        # Two-pass PIL validation: verify() invalidates the file handle, so
        # re-open to read dimensions and confirmed format.
        try:
            with Image.open(tmp) as img:
                img.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            return _err(400, "not a valid image", "corrupt")

        try:
            with Image.open(tmp) as img:
                actual_format = img.format or ""
                width, height = img.size
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            return _err(400, "not a valid image", "corrupt")

        if width <= 0 or height <= 0:
            return _err(400, "invalid image dimensions", "corrupt")
        if width > MAX_DIMENSION or height > MAX_DIMENSION:
            return _err(
                400,
                f"image too large: {width}x{height} (max {MAX_DIMENSION})",
                "too_big_dim",
            )

        actual_mime = PIL_FORMAT_TO_MIME.get(actual_format, "")
        if actual_mime != declared_mime:
            return _err(
                400,
                f"mime mismatch: declared {declared_mime}, actual {actual_mime or actual_format!r}",
                "invalid_mime",
            )

        try:
            tmp.replace(target)
        except OSError as e:
            return _err(500, f"rename failed: {e}", "internal")
    finally:
        # Cleanup any leftover tempfile. After a successful replace() the
        # tmp path no longer exists, so this is a no-op on the happy path.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    rel_path = f"/{project_id}/refs/{new_id}.{ext}"
    logger.info(
        "ref upload ok project=%s path=%s bytes=%d %dx%d %s",
        project_id, rel_path, size, width, height, declared_mime,
    )
    return web.json_response({
        "path": rel_path,
        "server_path": str(target),
        "bytes": size,
        "mime": declared_mime,
        "width": width,
        "height": height,
    })


def make_app(
    api_key: Optional[str] = None,
    projects_root: Optional[Path] = None,
) -> web.Application:
    """Build the asset-server aiohttp app.

    `api_key` enables the authenticated upload endpoint; if omitted the
    upload route still mounts but returns 401 to every caller. `projects_root`
    overrides the default projects/ location (computed relative to this file).
    Both have safe defaults so the standalone `python asset_server.py` entry
    point still works unchanged.
    """
    app = web.Application(middlewares=[cors_middleware])
    app["api_key"] = api_key or os.environ.get("API_SERVER_KEY", "")
    app["projects_root"] = (projects_root or _projects_root()).resolve()

    app.router.add_get("/health", health)
    app.router.add_options(
        "/{project_id}/refs/upload",
        upload_options_handler,
    )
    app.router.add_post(
        "/{project_id}/refs/upload",
        upload_reference_handler,
    )
    app.router.add_get(
        "/{project_id}/{subdir}/{rest:.*}",
        serve_asset,
    )
    app.router.add_route(
        "OPTIONS",
        "/{tail:.*}",
        lambda r: web.Response(status=204),
    )
    return app


def run(host: str = "127.0.0.1", port: int = 9120) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    app = make_app()
    logger.info(
        "asset server listening on http://%s:%d (root=%s)",
        host, port, _projects_root(),
    )
    web.run_app(app, host=host, port=port, print=None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprite Studio asset server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9120)
    args = parser.parse_args()
    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
