import json

import pytest

from bard.obsidian import vault_for


@pytest.fixture
def registry(tmp_path):
    def write(vaults) -> object:
        path = tmp_path / "obsidian.json"
        path.write_text(json.dumps({"vaults": vaults}))
        return path

    return write


def test_innermost_registered_vault_wins(tmp_path, registry):
    outer, inner = tmp_path / "backend", tmp_path / "backend" / "data" / "campaigns" / "c1"
    inner.mkdir(parents=True)
    path = registry({"aaaa": {"path": str(outer)}, "bbbb": {"path": str(inner), "open": True}})
    assert vault_for(inner, path) == {"id": "bbbb", "folder": ""}


def test_a_folder_inside_a_vault_gets_its_relative_path(tmp_path, registry):
    vault, folder = tmp_path / "Vault", tmp_path / "Vault" / "Campaigns" / "Morvath"
    folder.mkdir(parents=True)
    path = registry({"aaaa": {"path": str(vault)}, "cccc": {"path": str(tmp_path / "Vault2")}})
    assert vault_for(folder, path) == {"id": "aaaa", "folder": "Campaigns/Morvath"}


def test_unknown_folder_or_unreadable_registry_is_none(tmp_path, registry):
    folder = tmp_path / "campaign"
    folder.mkdir()
    assert vault_for(folder, registry({"aaaa": {"path": str(tmp_path / "other")}})) is None
    assert vault_for(folder, tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert vault_for(folder, tmp_path / "bad.json") is None
    assert vault_for(folder, registry({"bad id!": {"path": str(folder)}, "x": "junk"})) is None
