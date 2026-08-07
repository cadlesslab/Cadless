"""Forge-mode config: default-off kill-switch + budget-scaled N (C4).

Forge is default-OFF and opt-in. ``forge_enabled`` is a global kill-switch
(default False); ``forge_scaled_n()`` derives N from a budget rather than a
hard-coded constant, clamped to ``[forge_min_n, forge_max_n]``. These tests pin
the pure scaling math + the defaults so the live wiring (both-true gate) has a
trustworthy N to act on.
"""

from __future__ import annotations

from cadless.config import Settings


def test_forge_disabled_by_default():
    """The global kill-switch is OFF unless explicitly enabled."""
    assert Settings().forge_enabled is False


def test_forge_scaled_n_divides_budget_by_per_candidate_cost():
    """N = budget // per-candidate cost, within the clamp window."""
    s = Settings(forge_budget=12, forge_candidate_cost=3, forge_min_n=1, forge_max_n=8)
    assert s.forge_scaled_n() == 4


def test_forge_scaled_n_clamps_to_max():
    """A large budget never fans out beyond forge_max_n (cost ceiling)."""
    s = Settings(forge_budget=1000, forge_candidate_cost=1, forge_min_n=1, forge_max_n=5)
    assert s.forge_scaled_n() == 5


def test_forge_scaled_n_clamps_to_min():
    """A tiny budget never drops below forge_min_n (a race needs >=min samples)."""
    s = Settings(forge_budget=1, forge_candidate_cost=100, forge_min_n=2, forge_max_n=8)
    assert s.forge_scaled_n() == 2


def test_forge_scaled_n_handles_zero_cost_safely():
    """A zero/negative per-candidate cost degrades to forge_max_n, never divides by
    zero."""
    s = Settings(forge_budget=10, forge_candidate_cost=0, forge_min_n=1, forge_max_n=4)
    assert s.forge_scaled_n() == 4


def test_forge_scaled_n_default_is_a_real_race():
    """With shipped defaults, forge (when active) fans out more than one candidate."""
    assert Settings().forge_scaled_n() >= 2
