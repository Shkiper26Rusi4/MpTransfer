from pathlib import Path
import hashlib
import io
import re
import zipfile
import httpx
from PIL import Image
from .domain import norm

MAX_BYTES = 20 * 1024 * 1024
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def verify(data):
    if len(data) > MAX_BYTES:
        raise ValueError("Изображение превышает 20 МБ")
    with Image.open(io.BytesIO(data)) as im:
        im.verify()
    return data


def cache_photo(source, directory):
    if source.startswith(("https://", "http://")):
        from urllib.parse import urlsplit
        parsed = urlsplit(source)
        if parsed.username or parsed.password:
            raise ValueError("Ссылки с учётными данными запрещены")
        with httpx.stream("GET", source, timeout=30, follow_redirects=True) as response:
            response.raise_for_status()
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("Изображение превышает 20 МБ")
                chunks.append(chunk)
        data = b"".join(chunks)
    else:
        path = Path(source)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Изображение превышает 20 МБ")
        data = path.read_bytes()
    verify(data)
    with Image.open(io.BytesIO(data)) as im:
        suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(im.format)
    if not suffix:
        raise ValueError("Поддерживаются JPEG, PNG, WebP")
    dest = Path(directory) / (hashlib.sha256(data).hexdigest() + suffix)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)  # Exact original bytes; no image transformation.
    return str(dest)


def unpack_zip(path, directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    result = []
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 512 * 1024 * 1024:
            raise ValueError("Архив превышает 512 МБ")
        for item in archive.infolist():
            target = (directory / item.filename).resolve()
            if not target.is_relative_to(directory):
                raise ValueError("Небезопасный путь в ZIP")
            if item.is_dir() or target.suffix.lower() not in EXTENSIONS:
                continue
            if item.file_size > MAX_BYTES:
                raise ValueError("Изображение в ZIP превышает 20 МБ")
            data = verify(archive.read(item))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            result.append(str(target))
    return result


def match_files(products, files):
    assigned = {p.key: [] for p in products}
    ambiguous = []
    for file in files:
        stem = Path(file).stem.casefold()
        matches = []
        for p in products:
            ids = [s.casefold() for s in (p.article, p.sku) if s.strip()]
            if any(stem == s or re.fullmatch(re.escape(s) + r"[_ -]\d+", stem) for s in ids):
                matches.append(p.key)
        if len(matches) == 1:
            assigned[matches[0]].append(str(file))
        elif matches:
            ambiguous.append(str(file))
    return assigned, ambiguous
