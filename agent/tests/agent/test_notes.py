"""Run notes in the booker's words: workflow labels translated, pipeline talk dropped."""

from __future__ import annotations

import pytest

from draw.agent.notes import MAX_MODEL_NOTES, model_notes, plain_note, plain_notes


@pytest.mark.parametrize(
    ("note", "plain"),
    [
        (
            "compare_audiences[Synthetic Act]: Qloo returned no results.",
            "Audience comparison for Synthetic Act: Qloo returned no results.",
        ),
        (
            "where_popular[Synthetic Act]: QLOO_HTTP. Try later.",
            "Local heatmap for Synthetic Act: QLOO_HTTP. Try later.",
        ),
        (
            "trends[Act One, Act Two]: Forced degraded for the test.",
            "Trend data for Act One, Act Two: Forced degraded for the test.",
        ),
        (
            "generate_candidates (partial): Fewer acts than asked for.",
            "Candidate search (partial): Fewer acts than asked for.",
        ),
        (
            "rank: Qloo returned no row for 1 of 3 acts.",
            "Qloo ranking: Qloo returned no row for 1 of 3 acts.",
        ),
        # Already plain, or not a known label: unchanged.
        ("Skipped 'Synthetic Act': not confirmed as a Qloo entity.", None),
        ("note: something else", None),
    ],
)
def test_plain_note_translates_workflow_labels(note: str, plain: str | None) -> None:
    assert plain_note(note) == (plain or note)


def test_plain_notes_dedupes_after_translation() -> None:
    notes = ["rank: x", "rank: x", "", "Other."]
    assert plain_notes(notes) == ["Qloo ranking: x", "Other."]


def test_model_notes_drop_pipeline_narration() -> None:
    # The model's notes from a real shortlist run, with the profile id made synthetic.
    narrated = [
        "Signals read for the top 15 of 27 candidates; the rest score neutral on the Qloo signals.",
        "Used existing crowd profile prof_10fa8eb1; build_crowd_profile skipped as instructed.",
        "Resolved past acts were not passed to the pipeline; the profile was taken as given.",
        "generate_candidates returned 27 candidates (count ≥ 8, no broadening needed).",
        "gather_signals read Qloo signals for the top 15 of 27 candidates.",
        "Shortlist order is as returned by score_and_rank.",
        "Genre urn:tag:genre:music:shoegaze matched.",
    ]
    # Only the hard rules apply (tool names, ids, URNs, snake_case): the two notes that name
    # none of those survive, and the cap keeps it to two.
    assert model_notes(narrated) == [narrated[0], narrated[2]]


def test_model_notes_keep_plain_sentences_up_to_the_cap() -> None:
    plain = [
        "No Qloo genre matched 'darkwave', so it was left out.",
        "Only 6 candidates matched the genre, so the list leans on the crowd's related acts.",
        "A third plain note.",
        "No Qloo genre matched 'darkwave', so it was left out.",
    ]
    assert model_notes(plain) == plain[:MAX_MODEL_NOTES]
