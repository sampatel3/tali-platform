from app.services.evaluation_service import calculate_weighted_rubric_score


def test_weighted_rubric_score_calculation():
    rubric = {
        "understanding": {"weight": 0.4},
        "implementation": {"weight": 0.6},
    }
    scores = {"understanding": "excellent", "implementation": "good"}
    # (3*0.4 + 2*0.6) / 1.0 = 2.4
    assert calculate_weighted_rubric_score(scores, rubric) == 2.4
