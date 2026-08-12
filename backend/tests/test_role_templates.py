"""Role-template convergence: core vs growth, not five bespoke profiles."""

from __future__ import annotations

from app.db import load_app_config


def test_symbols_use_two_role_templates():
    cfg = load_app_config()
    by_id = {s.id: s for s in cfg.symbols}

    core_ids = ["HS300", "ZZ500", "SZ50"]
    growth_ids = ["CYB200", "KCB50"]

    core_pause = {by_id[i].strategy_profile.pause_percentile for i in core_ids}
    core_tiers = {tuple(by_id[i].strategy_profile.tier_mults) for i in core_ids}
    core_w = {
        tuple(sorted(by_id[i].strategy_profile.strategy_weights.items()))
        for i in core_ids
    }
    assert core_pause == {0.9}
    assert core_tiers == {(1.8, 1.4, 1.0, 0.5)}
    assert core_w == {(("trend", 0.3), ("valuation", 0.7))}

    growth_pause = {by_id[i].strategy_profile.pause_percentile for i in growth_ids}
    growth_tiers = {tuple(by_id[i].strategy_profile.tier_mults) for i in growth_ids}
    assert growth_pause == {0.8}
    assert growth_tiers == {(1.6, 1.3, 1.0, 0.4)}
    # fair band must be 1.0× (no sub-1 standard DCA for growth)
    assert by_id["KCB50"].strategy_profile.tier_mults[2] == 1.0
    # only KCB keeps full-sample percentile window
    assert by_id["CYB200"].strategy_profile.percentile_window == "5y"
    assert by_id["KCB50"].strategy_profile.percentile_window == "full"


def test_target_weights_sum_to_one():
    cfg = load_app_config()
    total = sum(s.target_weight for s in cfg.symbols)
    assert abs(total - 1.0) < 1e-9
