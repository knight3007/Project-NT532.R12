from test_scenario import make

from nt532.decider.state_text import BOTH, questions_from_state


def test_questions_from_state_matches_generator():
    recs = make(300)
    seen = set()
    for r in recs:
        got, want = questions_from_state(r["state"]), r["questions"]
        for q, spec in want.items():
            assert got[q] == spec, (r["id"], q)
            seen.add(q)
        if r["id"].endswith("decide"):
            # `nozzle` phụ thuộc truth nên có thể thừa với state gõ tay, nhưng chỉ khi có bia
            assert ("target" in got) == bool(r["obs"]["targets"])
            assert set(want) <= set(got)
        else:
            assert set(got) == {"after_verify"}
        if BOTH in want.get("nozzle", {}).get("candidates", []):
            seen.add("both")
    assert seen >= {"real_fire", "action", "target", "nozzle", "after_verify", "both"}


def test_no_targets_has_no_target_or_nozzle_question():
    state = "stage: decide\nalarm: s1\ndetections (3 frames): none\n"
    assert set(questions_from_state(state)) == {"real_fire", "action"}
