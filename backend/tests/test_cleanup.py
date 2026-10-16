"""Spelling cleanup: code finds candidates, the LLM settles ambiguous ones, fixes are visible."""

import pytest

from bard import cleanup
from bard.campaign import Entity

ENTITIES = [
    Entity("Elara", "pc", description="Theo's paladin."),
    Entity("Delara", "npc", description="An NPC Theo plays."),
    Entity("Auggie", "pc"),
    Entity("Magenta", "npc", aliases=["Maggie"]),
    Entity("Kessia", "pc", heard_as=["Cassia"]),
    Entity("Zorbo", "npc", status="suggested"),  # not confirmed: not a known spelling
]
DICT = frozenset(
    {"three", "four", "waves", "will", "go", "saving", "throw", "the", "bard", "sings", "i", "thunder", "wave", "hits", "elf"}
)


@pytest.fixture
def speller():
    return cleanup.Speller(ENTITIES, dictionary=DICT, dnd=[("Thunderwave", "spell"), ("Saving Throw", "term"), ("Bard", "term")])


def found(speller, text):
    return [(c.heard, c.options, c.sure) for c in speller.find(text)]


def test_two_similar_characters_are_left_to_the_llm(speller):
    assert found(speller, "Dallara, three.") == [("Dallara", ["Delara", "Elara"], False)]


def test_sure_fixes(speller):
    assert found(speller, "augie four") == [("augie", ["Auggie"], True)]  # near-identical, one option
    assert found(speller, "auggie four") == [("auggie", ["Auggie"], True)]  # case only
    assert found(speller, "Cassia waves") == [("Cassia", ["Kessia"], True)]  # an accepted heard-as spelling
    assert found(speller, "thunder wave hits") == [("thunder wave", ["Thunderwave"], True)]  # spacing


def test_left_alone(speller):
    assert found(speller, "Maggie waves") == []  # an alias is a real name
    assert found(speller, "I will go") == []
    assert found(speller, "a saving throw") == []  # everyday words, only the case differs
    assert found(speller, "the bard sings") == []
    assert found(speller, "Zorbo waves") == []  # suggested entries aren't known spellings yet
    assert found(speller, "Elara and Delara") == []


def test_close_but_not_sure_asks(speller):
    assert found(speller, "Magento waves") == [("Magento", ["Magenta"], False)]


def test_apply_moves_earlier_fixes_and_records_what_was_heard():
    text = "🐉 Dallara and augie"
    at = len(text[: text.index("augie")].encode("utf-16-le")) // 2
    earlier = {"start": at, "end": at + 5, "heard": "ogie"}  # UTF-16 offsets
    new, fixes = cleanup.apply(text, [earlier], [(2, 9, "Delara")])
    assert new == "🐉 Delara and augie"
    assert fixes == [{"start": 3, "end": 9, "heard": "Dallara"}, {"start": at - 1, "end": at + 4, "heard": "ogie"}]


def test_ask_takes_only_offered_answers(speller):
    cands = speller.find("Dallara, three. Magento waves.")

    def llm(system, user, schema):
        assert "Delara (npc: An NPC Theo plays.)" in user
        return {"answers": [{"q": 0, "choice": "Delara"}, {"q": 1, "choice": "Quillon"}, {"q": 7, "choice": "Elara"}]}

    picked = cleanup.ask(llm, speller, [], [("m1", "Theo", "Dallara, three. Magento waves.", cands)])
    assert picked == {("m1", 0): "Delara"}


def test_clean_record_sure_fixes_survive_an_llm_outage(speller):
    record = {
        "session": {"id": "x", "mechanics": 0},  # saved before mechanics were removed
        "messages": [
            {"id": "m1", "t_start": 0, "text": "augie rolls.", "speaker": "Jun", "annotations": []},
            {"id": "m2", "t_start": 1, "text": "Dallara, three.", "speaker": "Nora", "annotations": []},
        ],
    }

    def down(*a):
        raise ConnectionError("LM Studio is not running")

    new, fixed, decided = cleanup.clean_record(record, speller, down)
    assert [m["text"] for m in new["messages"]] == ["Auggie rolls.", "Dallara, three."]
    assert (fixed, decided) == (1, [])
    new, fixed, decided = cleanup.clean_record(record, speller, lambda *a: {"answers": [{"q": 0, "choice": "Delara"}]})
    assert new["messages"][1]["text"] == "Delara, three."
    assert new["messages"][1]["fixes"] == [{"start": 0, "end": 6, "heard": "Dallara"}]
    assert decided == [("Dallara", "Delara")]
    assert record["messages"][1]["text"] == "Dallara, three."  # the input isn't modified
    assert "mechanics" not in new["session"]
    assert all("annotations" not in m for m in new["messages"])


def test_learner_suggests_after_repeats():
    learn = cleanup.Learner(every=2)
    assert learn.note("Dallara", "Delara") is False
    assert learn.note("dallara", "Delara") is True
    assert learn.note("Dallara", "Delara") is False  # once


@pytest.fixture
def table():
    words = DICT | {"confuse", "poison", "teleport", "magical", "the", "echoes", "cast", "butte", "try", "it"}
    dnd = [
        ("Confusion", "spell"),
        ("Teleport", "spell"),
        ("Poisoned", "term"),
        ("Scrying", "spell"),
        ("Thunderwave", "spell"),
        ("Bulette", "creature"),
        ("Nondetection", "spell"),
    ]
    return cleanup.Speller(ENTITIES, dictionary=words, dnd=dnd)


@pytest.mark.parametrize(
    "text",
    ["Confusing.", "He teleports away.", "They got poisoned.", "immune to magical scrying", "Try it, B-U-T-T-E."],
)
def test_everyday_words_and_spellings_out_stay(table, text):
    assert found(table, text) == []


def test_phrases_keep_their_words(table):
    assert found(table, "The thunder wave echoes.") == [("thunder wave", ["Thunderwave"], True)]
    assert found(table, "cast non-detection") == [("non-detection", ["Nondetection"], True)]


# ------------------------------------------------------------- the big vocabulary

import json  # noqa: E402

from bard import dnd_terms  # noqa: E402
from bard.campaign import everyday  # noqa: E402


def test_a_local_list_adds_to_the_built_in_names(tmp_path, monkeypatch):
    out = tmp_path / "dnd_terms.json"
    out.write_text(json.dumps({"terms": [["Zorbo", "creature"], ["Fireball", "spell"], [3, "junk"]]}))
    monkeypatch.setattr(dnd_terms, "generated_path", lambda: out)
    assert ("Zorbo", "creature") in dnd_terms.terms()
    assert ("Thunderwave", "spell") in dnd_terms.terms()  # the built-in names stay
    assert [n for n, _ in dnd_terms.terms()].count("Fireball") == 1
    assert all(isinstance(n, str) for n, _ in dnd_terms.terms())


@pytest.mark.parametrize("word", ["purified", "healthier", "stronger", "bigger", "running", "fucking", "okay", "poisoned"])
def test_everyday_forms(word):
    assert everyday(word, frozenset({"purify", "healthy", "strong", "big", "run", "poison"}))


def test_big_vocabulary_is_strict(speller):
    vocab = [  # an SRD spell plus made-up names shaped like the cases that matter
        ("Arcanist's Magic Aura", "spell"),
        ("Fare Away", "term"),
        ("Shape Flame", "spell"),
        ("Stoneheart", "term"),
        ("Ky-rin", "creature"),
    ]
    words = DICT | {"arcane", "magic", "aura", "far", "away", "have", "flame", "carny"}
    sp = cleanup.Speller(ENTITIES, dictionary=words, dnd=vocab, keep=["T-Mobile"])
    assert found(sp, "the arkanist magic aura") == [("arkanist magic aura", ["Arcanist's Magic Aura"], False)]
    assert found(sp, "far away") == []  # rules terms aren't fuzzy-matched as phrases
    assert found(sp, "have flame") == []  # word by word, "have" isn't "shape"
    assert found(sp, "T-Mobile plan") == []  # an ignored word
    assert found(sp, "carny") == []  # official names need near-identical spelling, not just sound
