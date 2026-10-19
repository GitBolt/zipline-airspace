"""Download one day of adsb.lol history and keep points inside each metro's study box.

One pass over an archive writes every metro asked for, so a day is downloaded
once however many metros need it.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from survey.constants import STUDY_DATES
from survey.metros import METROS, Metro, get
from survey.traces import iter_trace_blobs, parse_trace_boxes

ROOT = Path(__file__).resolve().parents[2]
ARCHIVES = ROOT / "data" / "archives"

SCHEMA = pa.schema(
    [
        ("date", pa.string()),
        ("feed", pa.string()),
        ("icao", pa.string()),
        ("ts", pa.float64()),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        ("alt_baro", pa.float32()),
        ("alt_geom", pa.float32()),
        ("on_ground", pa.bool_()),
        ("source", pa.string()),
        ("category", pa.string()),
        ("type_code", pa.string()),
        ("db_flags", pa.int16()),
        ("gs", pa.float32()),
    ]
)


def release_names(date: str, kind: str) -> tuple[str, list[str]]:
    tag = f"v{date.replace('-', '.')}-planes-readsb-{kind}-0"
    if kind == "mlatonly":
        files = [f"{tag}.tar"]
    else:
        files = [f"{tag}.tar.aa", f"{tag}.tar.ab"]
    return tag, files


def ensure_archive(date: str, kind: str) -> list[Path]:
    tag, files = release_names(date, kind)
    ARCHIVES.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in files:
        path = ARCHIVES / name
        url = f"https://github.com/adsblol/globe_history_2026/releases/download/{tag}/{name}"
        if not path.exists() or path.stat().st_size < 1_000_000:
            print(f"download {name}")
            subprocess.run(
                ["curl", "-fL", "--retry", "3", "-C", "-", "-o", str(path), url],
                check=True,
            )
        paths.append(path)
    return paths


def raw_path(metro: Metro, date: str, kind: str) -> Path:
    return metro.raw_dir / f"{date}-{kind}.parquet"


class _Sink:
    """Buffered parquet output for one metro."""

    def __init__(self, out: Path):
        out.parent.mkdir(parents=True, exist_ok=True)
        self.out = out
        self.part = out.with_suffix(".parquet.partial")
        self.writer = pq.ParquetWriter(self.part, SCHEMA, compression="zstd")
        self.buffer: list[dict] = []
        self.kept = 0

    def add(self, rows: list[dict]) -> None:
        self.buffer.extend(rows)
        self.kept += len(rows)
        if len(self.buffer) >= 200_000:
            self.flush()

    def flush(self) -> None:
        if self.buffer:
            self.writer.write_table(pa.Table.from_pylist(self.buffer, schema=SCHEMA))
            self.buffer.clear()

    def finish(self) -> None:
        self.flush()
        self.writer.close()
        if self.kept == 0:
            self.part.unlink(missing_ok=True)
            pq.write_table(pa.Table.from_pylist([], schema=SCHEMA), self.out)
        else:
            self.part.replace(self.out)

    def abandon(self) -> None:
        self.writer.close()


def extract_day(date: str, kind: str, metros: list[Metro]) -> dict[str, int]:
    """Write `date`'s points for every metro that does not have them yet. Returns points kept per metro."""
    todo = []
    for metro in metros:
        out = raw_path(metro, date, kind)
        if out.exists() and out.stat().st_size > 0:
            print(f"have {metro.key} {out.name}")
        else:
            todo.append(metro)
    if not todo:
        return {}
    paths = ensure_archive(date, kind)
    boxes = {metro.key: metro.bbox() for metro in todo}
    sinks = {metro.key: _Sink(raw_path(metro, date, kind)) for metro in todo}
    files = 0
    errors = 0
    feed = "prod" if kind == "prod" else "mlatonly"
    try:
        for _name, blob in iter_trace_blobs([str(p) for p in paths]):
            files += 1
            try:
                found = parse_trace_boxes(blob, feed, boxes)
            except Exception:
                errors += 1
                continue
            for key, rows in found.items():
                for row in rows:
                    row["date"] = date
                sinks[key].add(rows)
            if files % 5000 == 0:
                kept = " ".join(f"{key} {sink.kept}" for key, sink in sinks.items())
                print(f"  {date} {kind} files {files} points {kept} errors {errors}")
    except BaseException:
        for sink in sinks.values():
            sink.abandon()
        raise
    for key, sink in sinks.items():
        sink.finish()
        if sink.kept == 0:
            print(f"no study-box points for {key} in {date} {kind} ({files} files). Wrote an empty table.")
        else:
            print(f"wrote {key} {sink.out.name} files {files} points {sink.kept} errors {errors}")
    for path in paths:
        path.unlink(missing_ok=True)
    return {key: sink.kept for key, sink in sinks.items()}


def extract_dates(dates: list[str], metros: list[Metro]) -> None:
    for date in dates:
        extract_day(date, "prod", metros)
        extract_day(date, "mlatonly", metros)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", nargs="*", default=STUDY_DATES)
    parser.add_argument("--metros", nargs="*", default=list(METROS), help=f"any of {', '.join(METROS)}")
    args = parser.parse_args()
    extract_dates(args.dates, [get(key) for key in args.metros])


if __name__ == "__main__":
    main()
