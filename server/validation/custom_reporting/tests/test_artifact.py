import pytest

from validation.custom_reporting.artifact import aggregate_digest, build_manifest, main


def test_manifest_excludes_runtime_files_and_is_stable(tmp_path):
    (tmp_path / "provider.py").write_text("x = 1\n")
    (tmp_path / "module.pyc").write_bytes(b"cache")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "provider.pyc").write_bytes(b"cache")

    first = build_manifest(tmp_path)
    second = build_manifest(tmp_path)

    assert [entry.path for entry in first] == ["provider.py"]
    assert first == second
    assert aggregate_digest(first) == aggregate_digest(second)


def test_cli_copies_only_manifest_files_and_verifies_digests(tmp_path, capsys):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    manifest = tmp_path / "overlay-sha256.txt"
    source.mkdir()
    destination.mkdir()
    (source / "nested").mkdir()
    (source / "provider.py").write_text("x = 1\n")
    (source / "nested" / "module.py").write_text("y = 2\n")
    (source / "module.pyc").write_bytes(b"cache")
    (destination / "stale.py").write_text("stale = True\n")

    exit_code = main(
        [
            "--source",
            str(source),
            "--destination",
            str(destination),
            "--manifest",
            str(manifest),
        ]
    )

    source_entries = build_manifest(source)
    destination_entries = build_manifest(destination)
    digest = aggregate_digest(source_entries)
    assert exit_code == 0
    assert destination_entries == source_entries
    assert [entry.path for entry in destination_entries] == [
        "nested/module.py",
        "provider.py",
    ]
    assert manifest.read_text().splitlines() == [
        f"# aggregate-sha256: {digest}",
        *[f"{entry.sha256}  {entry.path}" for entry in source_entries],
    ]
    output = capsys.readouterr().out
    assert f"source aggregate sha256: {digest}" in output
    assert f"destination aggregate sha256: {digest}" in output


@pytest.mark.parametrize("destination_is_parent", [True, False])
def test_cli_rejects_overlapping_source_and_destination_before_deletion(
    tmp_path,
    destination_is_parent,
):
    parent = tmp_path / "parent"
    child = parent / "child"
    parent.mkdir()
    child.mkdir()
    source, destination = (child, parent) if destination_is_parent else (parent, child)
    protected_file = source / "provider.py"
    protected_file.write_text("x = 1\n")
    destination_marker = destination / "do-not-delete.txt"
    destination_marker.write_text("keep\n")

    with pytest.raises(SystemExit, match="来源目录与目标目录不能重叠"):
        main(
            [
                "--source",
                str(source),
                "--destination",
                str(destination),
                "--manifest",
                str(tmp_path / "manifest.txt"),
            ]
        )

    assert protected_file.read_text() == "x = 1\n"
    assert destination_marker.read_text() == "keep\n"


@pytest.mark.parametrize("manifest_owner", ["source", "destination"])
def test_cli_rejects_manifest_inside_source_or_destination_before_deletion(
    tmp_path,
    manifest_owner,
):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "provider.py").write_text("x = 1\n")
    destination_marker = destination / "do-not-delete.txt"
    destination_marker.write_text("keep\n")
    owner = source if manifest_owner == "source" else destination

    with pytest.raises(SystemExit, match="清单文件不能位于来源目录或目标目录内"):
        main(
            [
                "--source",
                str(source),
                "--destination",
                str(destination),
                "--manifest",
                str(owner / "manifest.txt"),
            ]
        )

    assert destination_marker.read_text() == "keep\n"


@pytest.mark.parametrize("symlink_kind", ["file", "directory"])
def test_manifest_rejects_file_and_directory_symlinks(tmp_path, symlink_kind):
    root = tmp_path / "root"
    root.mkdir()
    if symlink_kind == "file":
        outside = tmp_path / "outside.py"
        outside.write_text("secret = True\n")
        (root / "linked.py").symlink_to(outside)
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.py").write_text("secret = True\n")
        (root / "linked-directory").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="制品目录不允许符号链接"):
        build_manifest(root)


@pytest.mark.parametrize("symlink_role", ["source", "destination"])
def test_cli_rejects_symlink_source_or_destination_before_deletion(
    tmp_path,
    symlink_role,
):
    real_source = tmp_path / "real-source"
    real_destination = tmp_path / "real-destination"
    real_source.mkdir()
    real_destination.mkdir()
    (real_source / "provider.py").write_text("x = 1\n")
    destination_marker = real_destination / "do-not-delete.txt"
    destination_marker.write_text("keep\n")
    source = real_source
    destination = real_destination
    if symlink_role == "source":
        source = tmp_path / "source-link"
        source.symlink_to(real_source, target_is_directory=True)
    else:
        destination = tmp_path / "destination-link"
        destination.symlink_to(real_destination, target_is_directory=True)

    with pytest.raises(SystemExit, match="来源目录或目标目录不能是符号链接"):
        main(
            [
                "--source",
                str(source),
                "--destination",
                str(destination),
                "--manifest",
                str(tmp_path / "manifest.txt"),
            ]
        )

    assert destination_marker.read_text() == "keep\n"
