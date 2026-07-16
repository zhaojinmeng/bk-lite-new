import argparse
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True)
class ArtifactEntry:
    path: str
    sha256: str


def build_manifest(root: Path) -> list[ArtifactEntry]:
    files = sorted(path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
    return [
        ArtifactEntry(
            str(path.relative_to(root)),
            sha256(path.read_bytes()).hexdigest(),
        )
        for path in files
    ]


def aggregate_digest(entries: list[ArtifactEntry]) -> str:
    payload = "".join(f"{entry.sha256}  {entry.path}\n" for entry in entries)
    return sha256(payload.encode()).hexdigest()


def _copy_manifest_files(
    source: Path,
    destination: Path,
    entries: list[ArtifactEntry],
) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for entry in entries:
        target = destination / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / entry.path, target)


def _write_manifest(path: Path, entries: list[ArtifactEntry]) -> None:
    digest = aggregate_digest(entries)
    payload = f"# aggregate-sha256: {digest}\n" + "".join(f"{entry.sha256}  {entry.path}\n" for entry in entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="固定并复制运行态 Enterprise overlay")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    source = args.source.resolve()
    destination = args.destination.resolve()
    if not source.is_dir():
        raise SystemExit(f"overlay 来源目录不存在: {source}")
    if source == destination:
        raise SystemExit("overlay 来源与目标不能相同")

    source_entries = build_manifest(source)
    _copy_manifest_files(source, destination, source_entries)
    destination_entries = build_manifest(destination)
    if destination_entries != source_entries:
        raise SystemExit("overlay 复制后逐文件 SHA-256 不一致")

    _write_manifest(args.manifest, source_entries)
    source_digest = aggregate_digest(source_entries)
    destination_digest = aggregate_digest(destination_entries)
    print(f"source aggregate sha256: {source_digest}")
    print(f"destination aggregate sha256: {destination_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
