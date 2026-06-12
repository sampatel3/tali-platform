"""Youden's-J threshold learner + pooling/shrinkage/clamps."""

from app.services.threshold_calibration.learner import (
    clamp_absolute,
    learn_threshold,
    shrink_and_clamp_to_org,
)


def _pairs(pos, neg):
    return [(s, 1) for s in pos] + [(s, 0) for s in neg]


def test_youden_picks_separating_threshold():
    pos = [70, 75, 80, 85, 90] * 3   # 15 positives, all >= 70
    neg = [10, 20, 30, 40, 45] * 6   # 30 negatives, all <= 45
    fit = learn_threshold(_pairs(pos, neg))
    assert fit is not None
    # perfectly separable → J = 1, cut sits in the (45, 70] gap
    assert 45 < fit.threshold <= 70
    assert fit.youden_j > 0.99
    assert fit.balanced_accuracy > 0.99


def test_below_floor_returns_none():
    # 3 positives < floor of 8
    assert learn_threshold(_pairs([70, 80, 90], [10, 20, 30] * 10)) is None


def test_imbalanced_not_degenerate():
    # heavy negative imbalance; J must still find the cut (not "reject everyone")
    pos = [72, 78, 84] * 4   # 12
    neg = [10] * 500
    fit = learn_threshold(_pairs(pos, neg))
    assert fit is not None
    assert fit.threshold <= 72   # accepts the positives
    assert fit.youden_j > 0.9


def test_shrink_pulls_sparse_role_toward_org():
    t_sparse, w_sparse = shrink_and_clamp_to_org(t_role=80, n_role=10, t_org=60)
    assert w_sparse < 0.2                 # low trust in a thin role
    assert 60 <= t_sparse <= 75           # stays near org, clamped up by <= 15
    t_rich, w_rich = shrink_and_clamp_to_org(t_role=70, n_role=500, t_org=60)
    assert w_rich > 0.9                   # trusts an abundant role
    assert t_rich > t_sparse              # moves toward its own value


def test_clamp_lowers_only_slightly():
    # a role wanting 40 (below org 60) can only be lowered by <= 5 → floored at 55
    t_final, _ = shrink_and_clamp_to_org(t_role=40, n_role=500, t_org=60)
    assert t_final >= 55


def test_clamp_absolute_band():
    assert clamp_absolute(40) == 50
    assert clamp_absolute(95) == 85
    assert clamp_absolute(70) == 70
