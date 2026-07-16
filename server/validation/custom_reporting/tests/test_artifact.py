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
