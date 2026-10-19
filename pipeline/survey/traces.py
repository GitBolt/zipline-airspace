"""Parse readsb trace archives into study-box points."""

from __future__ import annotations

import gzip
import io
import tarfile
from collections.abc import Iterator

import orjson

from survey.classify import normalize_source

BBOX = tuple[float, float, float, float]


def _open_tar(paths: list[str]):
    if len(paths) == 1:
        return tarfile.open(paths[0], "r:"), None
    import subprocess

    proc = subprocess.Popen(["cat", *paths], stdout=subprocess.PIPE)
    return tarfile.open(fileobj=proc.stdout, mode="r|"), proc


def iter_trace_blobs(paths: list[str]) -> Iterator[tuple[str, bytes]]:
    tf, proc = _open_tar(paths)
    try:
        for member in tf:
            name = member.name
            if not name.endswith(".json") or "/traces/" not in name.replace("\\", "/"):
                continue
            if member.size <= 0:
                continue
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            yield name, extracted.read()
    finally:
        tf.close()
        if proc is not None:
            proc.stdout.close()
            proc.wait()


def _num(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def parse_trace(blob: bytes, feed: str, bbox: BBOX) -> list[dict] | None:
    obj = _decode(blob)
    return None if obj is None else _rows(obj, feed, bbox)


def parse_trace_boxes(blob: bytes, feed: str, boxes: dict[str, BBOX]) -> dict[str, list[dict]]:
    """Rows for several boxes from one decode. Each box gets exactly what parse_trace would give it."""
    obj = _decode(blob)
    if obj is None:
        return {}
    out = {}
    for name, bbox in boxes.items():
        try:
            rows = _rows(obj, feed, bbox)
        except Exception:
            # Same as a single-box extraction, which skips a trace that fails to parse.
            continue
        if rows:
            out[name] = rows
    return out


def _decode(blob: bytes) -> dict | None:
    raw = gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob
    try:
        obj = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _rows(obj: dict, feed: str, bbox: BBOX) -> list[dict] | None:
    trace = obj.get("trace")
    if not isinstance(trace, list) or not trace:
        return None
    lon0, lat0, lon1, lat1 = bbox
    icao = str(obj.get("icao") or "")
    if not icao:
        return None
    root_flags = int(obj.get("dbFlags") or 0)
    root_type = obj.get("t") or ""
    category = ""
    type_code = root_type or ""
    db_flags = root_flags
    for point in trace:
        if not isinstance(point, list) or len(point) < 9:
            continue
        extra = point[8]
        if isinstance(extra, dict):
            if extra.get("category"):
                category = str(extra["category"])
            if extra.get("t"):
                type_code = str(extra["t"])
            if extra.get("dbFlags"):
                db_flags |= int(extra["dbFlags"])
    base_ts = float(obj.get("timestamp") or 0)
    rows: list[dict] = []
    for point in trace:
        if not isinstance(point, list) or len(point) < 3:
            continue
        lat = _num(point[1])
        lon = _num(point[2])
        if lat is None or lon is None:
            continue
        if lat < lat0 or lat > lat1 or lon < lon0 or lon > lon1:
            continue
        flags = int(point[6] or 0) if len(point) > 6 and isinstance(point[6], (int, float)) else 0
        if flags & 1:
            continue
        alt = point[3] if len(point) > 3 else None
        on_ground = alt == "ground"
        geom = _num(point[10]) if len(point) > 10 else None
        baro = None
        if isinstance(alt, (int, float)) and not isinstance(alt, bool):
            if flags & 8:
                geom = geom if geom is not None else float(alt)
            else:
                baro = float(alt)
        source = point[9] if len(point) > 9 and isinstance(point[9], str) else None
        extra = point[8] if len(point) > 8 and isinstance(point[8], dict) else None
        if extra:
            if extra.get("category"):
                category = str(extra["category"])
            if extra.get("t"):
                type_code = str(extra["t"])
        rows.append(
            {
                "icao": icao,
                "feed": feed,
                "ts": base_ts + float(point[0] or 0),
                "lat": lat,
                "lon": lon,
                "alt_baro": baro,
                "alt_geom": geom,
                "on_ground": on_ground,
                "source": normalize_source(source, feed),
                "category": category,
                "type_code": type_code,
                "db_flags": db_flags,
                "gs": _num(point[4]) if len(point) > 4 else None,
            }
        )
    return rows or None


def decode_member(blob: bytes) -> bytes:
    if blob[:2] == b"\x1f\x8b":
        return gzip.decompress(blob)
    return blob


def empty_stream() -> io.BytesIO:
    return io.BytesIO()
