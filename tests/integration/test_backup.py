import io
import json
import zipfile

import pytest
from test_analysis import output
from test_ocr_storage import complete
from test_web import web as web_fixture

from xilogue.config import Config
from xilogue.storage.backup import export_backup, restore_backup
from xilogue.storage.store import Store

web = web_fixture


def test_backup_restore_preserves_sources_and_derivatives(tmp_path):
    store = Store(tmp_path / "source", Config())
    try:
        item, _ = complete(store)
        store.queue_analysis()
        job = store.claim_analysis()
        store.finish_analysis(job, output(job["evidence"]), "test/local")
        archive = tmp_path / "snapshot.zip"
        export_backup(store, archive)
        assert restore_backup(archive, tmp_path / "restored")["files"] == 4
        restored = Store(tmp_path / "restored", Config())
        try:
            assert restored.document(item["id"])["text"] == store.document(item["id"])["text"]
            assert restored.insight(job["session_id"])["payload"] == output(job["evidence"])
        finally:
            restored.close()
        with pytest.raises(ValueError, match="new_directory"):
            restore_backup(archive, tmp_path / "restored")
        with pytest.raises(ValueError, match="destination_exists"):
            export_backup(store, archive)
    finally:
        store.close()


@pytest.mark.parametrize("mutation", ["traversal", "duplicate", "hash", "future", "missing_image"])
def test_restore_rejects_archive_without_publishing_directory(tmp_path, mutation):
    store = Store(tmp_path / "source", Config())
    try:
        complete(store)
        archive = tmp_path / "good.zip"
        export_backup(store, archive)
    finally:
        store.close()
    with zipfile.ZipFile(archive) as source:
        entries = {n: source.read(n) for n in source.namelist()}
    manifest = json.loads(entries["manifest.json"])
    if mutation == "traversal":
        entries["../outside.txt"] = b"unsafe"
    elif mutation == "hash":
        entries["config.json"] = b"{}"
    elif mutation == "future":
        manifest["schema"] = 999
    elif mutation == "missing_image":
        name = next(n for n in entries if n.startswith("images/"))
        entries.pop(name)
        manifest["files"].pop(name)
    entries["manifest.json"] = json.dumps(manifest).encode()
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as target:
        for n, b in entries.items():
            target.writestr(n, b)
        if mutation == "duplicate":
            with pytest.warns(UserWarning):
                target.writestr("config.json", b"{}")
    with pytest.raises(ValueError):
        restore_backup(broken, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".xilogue-restore-*"))


def test_web_backup_requires_pause_and_is_valid_archive(web):
    client, runtime, _ = web
    result = client.get("/api/backup")
    assert result.status_code == 200
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        assert "manifest.json" in archive.namelist()
    runtime.engine.start()
    assert client.get("/api/backup").status_code == 409
