"""The campaign vault: Obsidian markdown that Troubadour reads and writes without losing human edits."""

import os

import pytest

from troubadour.campaign import Entity, Vault, mention_counts, parse, render, safe_name


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "campaign")


def test_mention_counts_rank_a_regular_above_a_one_off(vault, tmp_path):
    vault.create("Elara", "pc")
    vault.create("Hank", "npc", aliases=["Dead"])
    vault.create("Cletus", "npc")
    sessions = tmp_path / "sessions"
    for sid, text in (("one", "Elara meets Hank. The dead man is not Hank."), ("two", "Elara goes on. Still dead.")):
        folder = sessions / sid
        folder.mkdir(parents=True)
        (folder / "transcript.json").write_text('{"messages":[{"text":"%s"}]}' % text)
    (vault.root / "Sessions").mkdir()
    (vault.root / "Sessions" / "one.md").write_text("---\nsession_id: one\n---\n[[Elara]] and [[Cletus]]\n")
    counts = mention_counts(vault, sessions)
    assert counts["Elara"] == 2  # both sessions; the note is the same session as the transcript
    assert counts["Hank"] == 1  # named once; the word "dead" is not Hank
    assert counts["Cletus"] == 1  # only the session note


def test_render_parse_round_trip():
    e = Entity(
        name="Magenta",
        type="npc",
        aliases=["Maggie"],
        heard_as=["Magento"],
        heard_as_suggested=["Majenta"],
        description="Tiefling bard who runs the Rusty Flagon.",
        facts=["Owes Kessia a favour. ([[2026-10-08 Session]])"],
        suggested_facts=["Might be a spy."],
    )
    back = parse("Magenta", render(e))
    assert back.to_json() == e.to_json()


def test_human_edits_survive_a_rewrite(vault):
    note = vault.root / "People" / "Magenta.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\ntype: npc\naliases: [Maggie]\nmood: grumpy\ntags: [villain, bard/npc]\n---\n"
        "# Magenta\nRuns the tavern.\n\n## Facts\n- Hates goblins\n  because of the fire.\n\n"
        "## My theories\nShe is secretly the queen.\n"
    )
    vault.add_fact("Magenta", "Has a pet raven.")
    e = vault.get("Magenta")
    assert e.facts == ["Hates goblins because of the fire.", "Has a pet raven."]
    text = note.read_text()
    assert "mood: grumpy" in text
    assert "villain" in text
    assert "## My theories\nShe is secretly the queen." in text


def test_old_bard_tag_is_replaced_by_the_troubadour_tag(vault):
    note = vault.root / "People" / "Magenta.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntype: npc\ntags: [villain, bard/npc]\n---\n# Magenta\nRuns the tavern.\n")
    assert vault.get("Magenta").tags == ["villain"]
    vault.add_fact("Magenta", "Has a pet raven.")
    assert "tags: [troubadour/npc, villain]" in note.read_text()


def test_unreadable_frontmatter_is_read_only_and_never_rewritten(vault):
    note = vault.root / "People" / "Broken.md"
    note.parent.mkdir(parents=True)
    original = "---\ntype: [npc\n---\n# Broken\n"
    note.write_text(original)
    e = vault.get("Broken")
    assert e.read_only
    with pytest.raises(ValueError, match="can't read"):
        vault.add_fact("Broken", "x")
    assert note.read_text() == original


def test_create_find_and_duplicates(vault):
    vault.create("Elara", "pc", heard_as=["Dallara", "Dilara"], aliases=["El"])
    assert vault.find("dilara").name == "Elara"
    assert vault.find("El").name == "Elara"
    assert vault.find("The Elara").name == "Elara"
    with pytest.raises(ValueError, match="already"):
        vault.create("el", "npc")  # an alias counts as taken
    with pytest.raises(ValueError, match="Unknown type"):
        vault.create("Zorbo", "wizard")


def test_rename_keeps_the_old_name_as_an_alias_and_moves_the_file(vault):
    vault.create("Augie", "pc")
    e = vault.update("Augie", name="Auggie", type="npc")
    assert e.name == "Auggie"
    assert "Augie" in e.aliases
    assert e.path == vault.root / "People" / "Auggie.md"
    assert not (vault.root / "People" / "Augie.md").exists()
    moved = vault.update("Auggie", type="place")
    assert moved.path == vault.root / "Places" / "Auggie.md"
    assert not (vault.root / "People" / "Auggie.md").exists()


def test_rename_onto_another_name_is_refused(vault):
    vault.create("Kessia", "pc")
    vault.create("Cassia", "npc")
    with pytest.raises(ValueError, match="already"):
        vault.update("Cassia", name="kessia")


def test_accepting_a_spelling_clears_it_from_suggestions(vault):
    vault.create("Magenta", "npc", heard_as_suggested=["Magento", "Majenta"])
    e = vault.update("Magenta", heard_as=["Magento"])
    assert e.heard_as_suggested == ["Majenta"]


def test_delete_moves_to_trash(vault):
    vault.create("Zorbo", "npc", description="first")
    vault.delete("Zorbo")
    vault.create("Zorbo", "npc", description="second")
    vault.delete("Zorbo")
    trashed = sorted(p.name for p in (vault.root / ".trash").iterdir())
    assert len(trashed) == 2
    with pytest.raises(KeyError):
        vault.get("Zorbo")


def test_merge_combines_and_trashes_the_duplicate(vault):
    vault.create("Cassia", "npc", heard_as=["Kasia"], facts=["Carries a lute."])
    vault.create("Kessia", "pc", facts=["carries a lute", "Is a she/her tiefling."])
    e = vault.merge("Cassia", into="Kessia")
    assert "Cassia" in e.aliases
    assert e.heard_as == ["Kasia"]
    assert e.facts == ["carries a lute", "Is a she/her tiefling."]  # near-duplicates folded
    assert vault.find("Cassia").name == "Kessia"
    assert len(vault.entities()) == 1


def test_add_fact_skips_near_duplicates(vault):
    vault.create("Elara", "pc")
    vault.add_fact("Elara", "Elara has a pet owl.")
    vault.add_fact("Elara", "elara has a pet owl")
    vault.add_fact("Elara", "Might be cursed.", suggested=True)
    e = vault.get("Elara")
    assert e.facts == ["Elara has a pet owl."]
    assert e.suggested_facts == ["Might be cursed."]


def test_edits_made_in_obsidian_are_picked_up(vault):
    e = vault.create("Elara", "pc")
    text = e.path.read_text().replace("heard_as: []", "heard_as: [Dilara]")
    e.path.write_text(text)
    os.utime(e.path, ns=(1, 1))  # a different mtime even on a coarse clock
    assert vault.find("Dilara").name == "Elara"


def test_campaign_facts(vault):
    vault.add_campaign_fact("The party is in the city of Dallon.")
    vault.add_campaign_fact("the party is in the city of dallon")
    assert vault.campaign_facts() == ["The party is in the city of Dallon."]


def test_write_note_never_overwrites_by_default(vault):
    a = vault.write_note("Notes", "Session 12", "one")
    b = vault.write_note("Notes", "Session 12", "two")
    assert (a.name, b.name) == ("Session 12.md", "Session 12 (2).md")
    c = vault.write_note("Notes", "Session 12", "three", overwrite=True)
    assert c == a
    assert a.read_text() == "three"


@pytest.mark.parametrize("bad", ["../evil", "a/b", "C:\\x", "[[link]]", "...", "  "])
def test_names_stay_inside_their_folder(vault, bad):
    try:
        name = safe_name(bad)
    except ValueError:
        return  # empty after cleaning: refused
    assert "/" not in name
    assert "\\" not in name
    assert not name.startswith(".")
    e = vault.create(bad, "npc")
    assert e.path.parent == vault.root / "People"


def test_index_lists_everything(vault):
    vault.create("Elara", "pc", heard_as=["Dilara"], description="Half-elf ranger.\nMore.")
    vault.create("Zorbo", "npc", status="suggested")
    vault.write_index()
    text = (vault.root / "Glossary.md").read_text()
    assert "| [[Elara]] |  | Dilara | Half-elf ranger. |" in text
    assert "[[Zorbo]] (suggested)" in text


# ------------------------------------------------------------- corrector

from troubadour.campaign import Corrector  # noqa: E402


def test_corrector_fixes_heard_as_and_case():
    entities = [
        Entity(name="Elara", type="pc", heard_as=["Dilara", "Dallara"]),
        Entity(name="Augie", type="pc", aliases=["Aug"]),
        Entity(name="Will", type="npc"),
        Entity(name="Rusty Flagon", type="place", heard_as=["rusty flaggon"]),
    ]
    fix = Corrector(entities, dictionary=frozenset({"will"}))
    assert fix("dilara, three. Dallara's turn.") == "Elara, three. Elara's turn."
    assert fix("augie and aug went") == "Augie and Aug went"
    assert fix("I will go") == "I will go"  # a name that's an English word isn't case-"fixed"
    assert fix("to the Rusty Flaggon!") == "to the Rusty Flagon!"
    assert fix("Dilaras and xDilara") == "Dilaras and xDilara"  # only whole words
    assert Corrector([])("unchanged") == "unchanged"


def test_readme_is_written_once_and_then_left_alone(vault):
    vault.write_index()
    readme = vault.root / "README.md"
    assert "## Layout" in readme.read_text()
    readme.write_text("my own words")
    vault.write_index()
    assert readme.read_text() == "my own words"


def test_review_decisions_are_changes_not_replacements(vault):
    vault.create(
        "Elara", "pc", heard_as_suggested=["Dallara", "Dilara", "Delilah"], suggested_facts=["Has an owl.", "Is cursed."]
    )
    vault.review("Elara", accept_spellings=["Dallara"])
    vault.review("Elara", accept_spellings=["Dilara"])  # a second quick click keeps the first
    vault.review("Elara", reject_spellings=["Delilah"], accept_facts=["Has an owl."], reject_facts=["Is cursed."])
    e = vault.get("Elara")
    assert e.heard_as == ["Dallara", "Dilara"]
    assert e.heard_as_rejected == ["Delilah"]
    assert e.heard_as_suggested == []
    assert e.facts == ["Has an owl."]
    assert e.suggested_facts == []


def test_ignored_words_round_trip_and_survive_hand_edits(vault):
    vault.ignore(["T-Mobile", "MLM"])
    vault.ignore(["t-mobile"])  # already there
    assert vault.ignored() == ["T-Mobile", "MLM"]
    text = vault.ignored_path.read_text()
    vault.ignored_path.write_text(text + "- Pickleball\n")  # added by hand in Obsidian
    assert vault.unignore("mlm") == ["T-Mobile", "Pickleball"]
