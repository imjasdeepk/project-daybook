"""The diary and knowledge base. Writes go through the CLI, assertions through
the library -- the same split the ledger tests use."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from daybook_tools import notes
from daybook_tools.cli import main
from daybook_tools.files import DaybookError


@pytest.fixture
def notes_root(tmp_path, monkeypatch):
    """A notes folder with no ledger anywhere near it."""
    for leftover in ("DAYBOOK_ROOT", "DAYBOOK_NOTES_ROOT"):
        monkeypatch.delenv(leftover, raising=False)
    root = tmp_path / "notes"
    monkeypatch.setenv("DAYBOOK_NOTES_ROOT", str(root))
    monkeypatch.chdir(tmp_path)
    assert main(["note", "init", str(root), "--period", "week", "--no-remember"]) == 0
    return root


def write(*args, expect: int = 0):
    assert main(["note", *args]) == expect


def add(title, **kwargs):
    args = ["add", "--title", title, "--no-commit"]
    for key, value in kwargs.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    write(*args)


# ----------------------------------------------------------------- the basics

def test_a_note_round_trips_through_the_files(notes_root):
    add("Ingest pipeline cutover", kind="log", who="Alice Chen, Ravi",
        tags="infra, oncall", date="2026-09-11", time="09:14",
        body="Cut over at 09:02. Rollback was the old consumer group.",
        source="note that we cut over the pipeline this morning")

    stored = notes.load_notes(notes_root)
    assert len(stored) == 1
    note = stored[0]
    assert note.title == "Ingest pipeline cutover"
    assert note.date == date(2026, 9, 11)
    assert note.time == "09:14"
    assert note.kind == "log"
    assert note.who == ["Alice Chen", "Ravi"]
    assert note.tags == ["infra", "oncall"]
    assert note.source == "note that we cut over the pipeline this morning"
    assert "old consumer group" in note.body
    assert note.citation == "2026/2026-W37.md:4"

    # The citation must actually point at the heading line it claims.
    lines = (notes_root / "2026" / "2026-W37.md").read_text(encoding="utf-8").splitlines()
    assert lines[note.line - 1].startswith("## 2026-09-11 09:14")

    found = notes.find(notes_root, "consumer group")
    assert found["matches"] == 1
    assert found["results"][0]["citation"] == note.citation
    assert notes.find_by_citation(notes_root, note.citation).title == note.title


def test_month_mode_files_by_month_instead(tmp_path, monkeypatch):
    for leftover in ("DAYBOOK_ROOT", "DAYBOOK_NOTES_ROOT"):
        monkeypatch.delenv(leftover, raising=False)
    root = tmp_path / "monthly"
    monkeypatch.setenv("DAYBOOK_NOTES_ROOT", str(root))
    monkeypatch.chdir(tmp_path)
    assert main(["note", "init", str(root), "--period", "month", "--no-remember"]) == 0

    add("Quarter close", date="2026-09-11")
    add("Board pack", date="2026-09-28")

    assert (root / "2026" / "2026-09.md").exists()
    assert not list(root.glob("2026/*W*.md"))
    assert len(notes.load_notes(root)) == 2


def test_a_title_is_required_because_it_is_all_the_index_shows(notes_root):
    with pytest.raises(DaybookError, match="needs a title"):
        notes.add_note(notes_root, notes.Note(date=date(2026, 9, 11), time="09:00",
                                              title="   "), period="week")


# -------------------------------------------------------- standalone, no ledger

def test_notes_work_with_no_ledger_and_never_import_beancount(notes_root):
    """The whole point of the split: somebody who wants a diary should not have
    to own a ledger, and `daybook note` must not drag Beancount in."""
    import subprocess
    import sys

    add("Standalone", body="No ledger in sight.")
    assert len(notes.load_notes(notes_root)) == 1
    assert not list(notes_root.rglob("*.beancount"))

    proof = subprocess.run(
        [sys.executable, "-c",
         "import sys; from daybook_tools import notes, files;"
         " sys.exit(1 if any('beancount' in m for m in sys.modules) else 0)"],
        capture_output=True, cwd=Path(__file__).resolve().parent.parent,
    )
    assert proof.returncode == 0, "importing notes pulled in beancount"


# --------------------------------------------------------------- the indexes

def _index_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("INDEX*.md")}


def test_one_write_touches_only_its_own_four_files(notes_root):
    """The fast-write guarantee: cost is bounded by the month, never by how
    much has been written before. Easy to lose silently in a later change."""
    add("First", date="2026-09-11")
    before = {
        p.relative_to(notes_root).as_posix(): p.stat().st_mtime_ns
        for p in notes_root.rglob("*") if p.is_file()
    }

    add("Second", date="2026-09-12")

    after = {
        p.relative_to(notes_root).as_posix(): p.stat().st_mtime_ns
        for p in notes_root.rglob("*") if p.is_file()
    }
    changed = {
        name for name, stamp in after.items()
        if before.get(name) != stamp
    }
    assert changed == {
        "2026/2026-W37.md",          # the note itself
        "2026/INDEX-2026-09.md",     # its month index
        "2026/INDEX-2026.md",        # the year rollup
        "INDEX.md",                  # the root rollup
    }, changed


def test_an_unchanged_rollup_is_not_rewritten(notes_root):
    """On a synced folder an identical rewrite still costs an upload and can
    still produce a conflicted copy, so not writing is the point."""
    target = notes_root / "2026" / "INDEX-2026.md"
    add("First", date="2026-09-11", tags="infra")
    add("Second", date="2026-09-12", tags="infra")
    stamp = target.stat().st_mtime_ns
    text = target.read_text(encoding="utf-8")

    # Re-running the refresh with nothing changed must not touch the file.
    notes.refresh_year(notes_root, 2026)
    assert target.stat().st_mtime_ns == stamp
    assert target.read_text(encoding="utf-8") == text


def test_indexes_are_derived_so_deleting_them_loses_nothing(notes_root):
    add("Cutover", date="2026-09-11", tags="infra", who="Alice")
    add("Postmortem", date="2026-08-02", tags="infra, adr", who="Ravi")
    add("Trip", date="2026-10-03", tags="travel")
    original = {name: (notes_root / name).read_text(encoding="utf-8")
                for name in _index_files(notes_root)}
    assert original, "there should be indexes to delete"

    for name in original:
        (notes_root / name).unlink()
    report = notes.reindex(notes_root)

    assert report["notes"] == 3
    assert _index_files(notes_root) == set(original)
    for name, text in original.items():
        assert (notes_root / name).read_text(encoding="utf-8") == text, name


def test_an_entry_typed_in_by_hand_is_found(notes_root):
    """It is still just Markdown in your folder. Editing it in a text editor
    on a phone has to work."""
    add("Written by the tool", date="2026-09-11")
    page = notes_root / "2026" / "2026-W37.md"
    page.write_text(
        page.read_text(encoding="utf-8")
        + "\n## 2026-09-12 21:30  Written by hand\n"
          "kind: idea\ntags: shower-thought\n\n"
          "Typed straight into the file on a train.\n",
        encoding="utf-8",
    )

    # Reads come from the Markdown, so it is found before any reindex.
    assert notes.find(notes_root, "train")["matches"] == 1
    notes.reindex(notes_root)
    rows = notes.parse_month_index(notes_root / "2026" / "INDEX-2026-09.md")
    assert [r["title"] for r in rows] == ["Written by the tool", "Written by hand"]


def test_a_back_dated_note_is_filed_in_its_own_month(notes_root):
    add("September", date="2026-09-11")
    add("July", date="2026-07-02")

    july = notes.parse_month_index(notes_root / "2026" / "INDEX-2026-07.md")
    september = notes.parse_month_index(notes_root / "2026" / "INDEX-2026-09.md")
    assert [r["title"] for r in july] == ["July"]
    assert [r["title"] for r in september] == ["September"]

    # And the month index stays in date order when the older note lands second.
    add("Earlier that September", date="2026-09-01")
    september = notes.parse_month_index(notes_root / "2026" / "INDEX-2026-09.md")
    assert [r["date"] for r in september] == ["2026-09-01", "2026-09-11"]


def test_a_week_straddling_two_months_indexes_each_note_by_its_own_date(notes_root):
    """Week 40 of 2026 runs 28 September to 4 October. The file is organised by
    week; the indexes are organised by the date on each entry."""
    add("Last of September", date="2026-09-30")
    add("First of October", date="2026-10-01")

    page = notes_root / "2026" / "2026-W40.md"
    assert page.exists(), sorted(p.name for p in (notes_root / "2026").iterdir())
    assert len(notes.parse_file(page, notes_root)) == 2

    september = notes.parse_month_index(notes_root / "2026" / "INDEX-2026-09.md")
    october = notes.parse_month_index(notes_root / "2026" / "INDEX-2026-10.md")
    assert [r["title"] for r in september] == ["Last of September"]
    assert [r["title"] for r in october] == ["First of October"]
    # Both cite the one file that actually holds them.
    assert september[0]["citation"].startswith("2026/2026-W40.md:")
    assert october[0]["citation"].startswith("2026/2026-W40.md:")


# ------------------------------------------------------- the knowledge base

def test_a_topic_is_a_query_over_tags(notes_root):
    add("Cutover", date="2026-09-11", tags="infra, oncall", who="Alice Chen")
    add("Lag spike postmortem", date="2026-08-02", tags="infra, adr", who="Ravi")
    add("Drop the v1 consumer", date="2026-07-15", tags="infra, adr", who="Alice Chen")
    add("Tokyo", date="2026-09-12", tags="travel")

    found = notes.topic(notes_root, "infra")
    assert found["matches"] == 3
    assert found["first"] == "2026-07-15"
    assert found["last"] == "2026-09-11"
    assert [r["title"] for r in found["results"]] == [
        "Cutover", "Lag spike postmortem", "Drop the v1 consumer",
    ]
    # Hand-counted: adr twice, oncall once; travel is not on an infra note.
    assert found["also_tagged"] == [{"tag": "adr", "notes": 2},
                                    {"tag": "oncall", "notes": 1}]
    assert found["people"] == [{"name": "Alice Chen", "notes": 2},
                               {"name": "Ravi", "notes": 1}]


def test_tallies_count_what_is_actually_written(notes_root):
    add("One", date="2026-09-11", tags="infra", who="Alice", kind="log")
    add("Two", date="2026-09-12", tags="infra, travel", who="Alice, Ravi", kind="log")
    add("Three", date="2026-09-13", tags="travel", kind="idea")

    assert notes.tally(notes_root, "tags")["tags"] == [
        {"name": "infra", "notes": 2}, {"name": "travel", "notes": 2},
    ]
    assert notes.tally(notes_root, "people")["people"] == [
        {"name": "Alice", "notes": 2}, {"name": "Ravi", "notes": 1},
    ]
    assert notes.tally(notes_root, "kinds")["kinds"] == [
        {"name": "log", "notes": 2}, {"name": "idea", "notes": 1},
    ]


# -------------------------------------------------------------- dated notes

def test_agenda_is_inclusive_at_both_ends(notes_root):
    add("Starts today", date="2026-09-01", when="2026-09-11")
    add("Last day in window", date="2026-09-01", when="2026-09-21")
    add("One day too late", date="2026-09-01", when="2026-09-22")
    add("Already over", date="2026-09-01", when="2026-09-10")
    add("Spans the window edge", date="2026-09-01", when="2026-09-08..2026-09-12")
    add("No date at all", date="2026-09-01")

    window = notes.agenda(notes_root, days=10, frm=date(2026, 9, 11))
    assert [e["title"] for e in window["entries"]] == [
        "Spans the window edge", "Starts today", "Last day in window",
    ]
    assert window["entries"][1]["days_until"] == 0
    assert window["entries"][2]["days_until"] == 10


def test_an_amendment_appends_and_leaves_the_original_alone(notes_root):
    add("Cutover", date="2026-09-11", body="Rolled back at 09:40.")
    original = notes.load_notes(notes_root)[0]
    before = (notes_root / "2026" / "2026-W37.md").read_text(encoding="utf-8")

    notes.amend(notes_root, original.citation, "It was 09:45, not 09:40.",
                period="week", at=date(2026, 9, 12))

    after = (notes_root / "2026" / "2026-W37.md").read_text(encoding="utf-8")
    assert after.startswith(before), "the original text must be untouched"
    assert notes.find_by_citation(notes_root, original.citation).body == "Rolled back at 09:40."
    amendment = notes.load_notes(notes_root)[-1]
    assert amendment.facets["amends"] == original.citation
    assert amendment.kind == "amendment"


def test_amending_a_citation_that_does_not_exist_says_so(notes_root):
    add("Cutover", date="2026-09-11")
    with pytest.raises(DaybookError, match="No note at"):
        notes.amend(notes_root, "2026/2026-W37.md:999", "nope", period="week")
    with pytest.raises(DaybookError, match="not a citation"):
        notes.find_by_citation(notes_root, "not-a-citation")


# ------------------------------------------------------------------ searching

def test_find_filters_stack_and_rank_puts_the_title_match_first(notes_root):
    add("Pipeline design", date="2026-09-11", tags="infra", who="Alice",
        body="Nothing about trains here.")
    add("Commute", date="2026-09-12", tags="personal", who="Ravi",
        body="Read about the pipeline on the train.")
    add("Pipeline retro", date="2026-08-02", tags="infra", who="Ravi")

    ranked = notes.find(notes_root, "pipeline")
    assert ranked["matches"] == 3
    assert ranked["results"][0]["title"] == "Pipeline design"

    narrowed = notes.find(notes_root, "pipeline", tags=["infra"], who=["Ravi"])
    assert [r["title"] for r in narrowed["results"]] == ["Pipeline retro"]

    dated = notes.find(notes_root, "", since=date(2026, 9, 1))
    assert {r["title"] for r in dated["results"]} == {"Pipeline design", "Commute"}


def test_prose_may_contain_markdown_headings(notes_root):
    """A `## ` in somebody's body must stay prose, not split their note in two."""
    add("Meeting notes", date="2026-09-11",
        body="## Agenda\n- one\n- two\n\n## Decisions\nShip it.")
    stored = notes.load_notes(notes_root)
    assert len(stored) == 1
    assert "## Decisions" in stored[0].body


def test_a_body_that_looks_like_another_entrys_heading_stays_one_note(notes_root):
    """A body containing another entry's full heading shape (date, time and
    title) would, unescaped, be parsed as a second entry -- silently forking
    one note into two and shifting every citation after it. This is the exact
    shape `HEADING` matches, unlike the plain `## Decisions` case above."""
    add("Real note", date="2026-09-11",
        body="She pasted this in:\n## 2026-09-16 10:00  Forged entry\nand kept typing.")
    stored = notes.load_notes(notes_root)
    assert len(stored) == 1
    assert stored[0].title == "Real note"
    assert "Forged entry" in stored[0].body

    # The month index agrees: one entry, not two.
    rows = notes.parse_month_index(notes_root / "2026" / "INDEX-2026-09.md")
    assert len(rows) == 1

    # The citation still resolves to the original title -- nothing shifted.
    found = notes.find_by_citation(notes_root, stored[0].citation)
    assert found.title == "Real note"


def test_a_note_with_an_escaped_heading_survives_reindex_byte_for_byte(notes_root):
    """Extends the indexes-are-derived guarantee to the escaped-body case: the
    escaping happens once, at write time, so deleting and rebuilding the
    indexes must not re-escape (or un-escape) anything."""
    add("Real note", date="2026-09-11",
        body="She pasted this in:\n## 2026-09-16 10:00  Forged entry\nand kept typing.")
    before = {name: (notes_root / name).read_text(encoding="utf-8")
             for name in _index_files(notes_root)}
    assert before

    for name in before:
        (notes_root / name).unlink()
    notes.reindex(notes_root)

    for name, text in before.items():
        assert (notes_root / name).read_text(encoding="utf-8") == text, name


def test_an_amendment_quoting_the_original_heading_stays_one_amendment(notes_root):
    """The same forgery risk applies when a correction quotes the original
    note back, heading and all."""
    add("Cutover", date="2026-09-11", body="Rolled back at 09:40.")
    original = notes.load_notes(notes_root)[0]

    quoted_back = (
        "Correcting:\n"
        f"## {original.date.isoformat()} {original.time}  {original.title}\n"
        "It actually said 09:45."
    )
    notes.amend(notes_root, original.citation, quoted_back, period="week",
               at=date(2026, 9, 12))

    stored = notes.load_notes(notes_root)
    assert len(stored) == 2  # the original, plus exactly one amendment
    assert stored[-1].kind == "amendment"
    assert stored[-1].facets["amends"] == original.citation


# --------------------------------------------------------------------- doctor

def test_doctor_reports_sync_conflicts_without_touching_them(notes_root):
    add("Cutover", date="2026-09-11")
    intruder = notes_root / "2026" / "2026-W37 (conflicted copy 2026-09-11).md"
    intruder.write_text("## 2026-09-11 09:14  Someone else's version\n", encoding="utf-8")

    report = notes.find_conflicts(notes_root)
    assert report["status"] == "needs_attention"
    assert report["sync_conflicts"] == ["2026/2026-W37 (conflicted copy 2026-09-11).md"]
    assert intruder.exists(), "a conflicted copy must never be deleted for you"
    # And it is not silently treated as notes.
    assert notes.find(notes_root, "Someone else")["matches"] == 0

    intruder.unlink()
    assert notes.find_conflicts(notes_root)["status"] == "ok"


def test_doctor_notices_a_stale_index(notes_root):
    add("Cutover", date="2026-09-11")
    (notes_root / "2026" / "INDEX-2026-09.md").write_text("# wrong\n", encoding="utf-8")
    assert notes.find_conflicts(notes_root)["stale_indexes"] == ["2026/INDEX-2026-09.md"]
    notes.reindex(notes_root)
    assert notes.find_conflicts(notes_root)["status"] == "ok"


# -------------------------------------------------- better together, fine apart

@pytest.fixture
def notes_beside_a_ledger(ledger_root, monkeypatch):
    """A notes folder inside a real ledger's records folder."""
    monkeypatch.delenv("DAYBOOK_NOTES_ROOT", raising=False)
    assert main(["entity", "add", "--name", "Robert Diaz",
                 "--aliases", "dad, papa", "--currency", "INR"]) == 0
    assert main(["entity", "add", "--name", "Dan Ortiz",
                 "--aliases", "ortiz", "--currency", "INR"]) == 0
    assert main(["note", "init", str(ledger_root / "notes"),
                 "--period", "week", "--no-remember"]) == 0
    return ledger_root / "notes"


def test_notes_default_to_the_daybook_folder_and_expand_aliases(notes_beside_a_ledger):
    """With a ledger reachable, a spoken name becomes the name the ledger uses,
    so the person in your diary is the same person who owes you money."""
    assert notes.notes_root() == notes_beside_a_ledger.resolve()

    add("Lunch", date="2026-09-11", who="papa")
    stored = notes.load_notes(notes_beside_a_ledger)[0]
    assert stored.who == ["Robert Diaz"], "the alias should resolve to the full name"


def test_an_ambiguous_name_stops_rather_than_picking(notes_beside_a_ledger):
    """The ledger's rule, applied to prose: never choose between candidates."""
    write("add", "--title", "Lunch", "--date", "2026-09-11", "--who", "robet",
          "--no-commit", expect=1)
    assert notes.load_notes(notes_beside_a_ledger) == []


def test_an_unknown_name_is_kept_as_typed(notes_beside_a_ledger):
    """A diary is not an accounts system: you can mention someone who has no
    record without being made to create one first."""
    add("Coffee", date="2026-09-11", who="Some Stranger")
    assert notes.load_notes(notes_beside_a_ledger)[0].who == ["Some Stranger"]
