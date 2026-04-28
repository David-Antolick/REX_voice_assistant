"""Action registry: correctness + perf invariants.

Run with: ``pytest test_actions.py -v``
Add ``-s`` to also print the perf numbers.

Two jobs in one file:

1. **Correctness.** Catch drift between @action declarations and reality:
   unique names, regex patterns compile, declared examples actually match
   their action, ArgSpec count agrees with regex capture-group count, no
   intra-backend phrase collisions, slot filtering works.
2. **Performance.** Hard ceilings on the dispatch hot path so a future
   change can't quietly add latency. A failure here means a regression —
   investigate before raising the ceiling.

This file is the gate. The live ``rex_main/benchmark.py`` records
production telemetry during real runs; the two are complementary.
"""

from __future__ import annotations

import re
import time

import pytest

from rex_main import actions, matcher


def _all_examples() -> list[tuple[str, str]]:
    return [(s.name, ex) for s in actions.all_specs() for ex in s.examples]


def _group_count(pattern_src: str) -> int:
    return re.compile(pattern_src).groups


# Correctness

def test_at_least_one_action_registered():
    assert len(actions.all_specs()) > 0, "No actions registered — backend imports broken?"


def test_action_names_unique():
    names = [s.name for s in actions.all_specs()]
    assert len(names) == len(set(names)), f"Duplicate names: {sorted(names)}"


@pytest.mark.parametrize("spec", actions.all_specs(), ids=lambda s: s.name)
def test_patterns_compile(spec):
    for src in spec.patterns:
        re.compile(src, re.I)


@pytest.mark.parametrize("spec_name,example", _all_examples(),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_examples_match_their_action(spec_name, example):
    spec = actions.find_by_name(spec_name)
    assert spec is not None, f"Spec {spec_name} disappeared from registry"
    matched = any(re.compile(p, re.I).match(example) for p in spec.patterns)
    assert matched, (
        f"Example {example!r} does not match any pattern of {spec_name}. "
        f"Either the example is wrong or the regex needs updating."
    )


@pytest.mark.parametrize("spec", actions.all_specs(), ids=lambda s: s.name)
def test_arg_count_matches_capture_groups(spec):
    if not spec.patterns:
        return
    counts = {_group_count(p) for p in spec.patterns}
    assert len(counts) == 1, (
        f"{spec.name} patterns have inconsistent capture-group counts: {counts}"
    )
    captured = next(iter(counts))
    assert captured == len(spec.args), (
        f"{spec.name}: regex captures {captured} group(s) but ArgSpec count is "
        f"{len(spec.args)}. Update spec.args to match."
    )


@pytest.mark.parametrize("spec", actions.all_specs(), ids=lambda s: s.name)
def test_handler_is_callable(spec):
    assert callable(spec.handler), f"{spec.name} handler is not callable"


def test_no_phrase_collision_within_a_backend():
    """Within one backend, no two actions should match the same example phrase.
    Cross-backend collisions are by design (capability shared across backends)."""
    by_backend: dict[str, list] = {}
    for spec in actions.all_specs():
        by_backend.setdefault(spec.backend, []).append(spec)

    failures: list[str] = []
    for backend, specs in by_backend.items():
        for spec in specs:
            for ex in spec.examples:
                hitters = [
                    other.name for other in specs
                    if any(re.compile(p, re.I).match(ex) for p in other.patterns)
                ]
                if len(hitters) != 1:
                    failures.append(
                        f"  backend={backend} example={ex!r} matches: {hitters}"
                    )
    assert not failures, "Phrase collisions within a backend:\n" + "\n".join(failures)


# Slot routing

def test_active_specs_filter_by_slot():
    actions.set_active_backend("music", "ytmd")
    active = {s.name for s in actions.active_specs()}
    assert any(n.startswith("ytmd_") for n in active)
    assert not any(n.startswith("spotify_") for n in active)

    actions.set_active_backend("music", "spotify")
    active = {s.name for s in actions.active_specs()}
    assert any(n.startswith("spotify_") for n in active)
    assert not any(n.startswith("ytmd_") for n in active)


def test_matcher_rebuilds_on_slot_change():
    actions.set_active_backend("music", "ytmd")
    ytmd_count = len(matcher.COMMAND_PATTERNS)
    actions.set_active_backend("music", "spotify")
    spotify_count = len(matcher.COMMAND_PATTERNS)
    assert ytmd_count > 0 and spotify_count > 0
    assert ytmd_count != spotify_count, (
        "Pattern count did not change on slot swap — rebuild hook not firing?"
    )


# Performance ceilings
# A failure here means a real regression. Investigate the cause before
# bumping a ceiling. Numbers are µs/ms with significant headroom over the
# measurements taken when the ceilings were set.

DISPATCH_CEILING_US = 50.0      # measured: ~1.7 µs
LOOKUP_CEILING_US = 5.0         # measured: ~0.3 µs
REBUILD_CEILING_MS = 50.0       # measured: ~5 ms at 32 specs


def test_perf_dispatch_per_match(capsys):
    actions.set_active_backend("music", "spotify")
    texts = [
        "play music",
        "volume 50",
        "this is so sad",
        "search bohemian rhapsody by queen",
        "xyzz no match",
    ]
    iters = 5000
    t0 = time.perf_counter()
    for _ in range(iters):
        for text in texts:
            for pattern, _name, _handler in matcher._DISPATCH_TABLE:
                if pattern.match(text):
                    break
    per_us = (time.perf_counter() - t0) * 1e6 / (iters * len(texts))
    with capsys.disabled():
        print(f"\n  dispatch per match: {per_us:.2f} µs (ceiling {DISPATCH_CEILING_US})")
    assert per_us < DISPATCH_CEILING_US


def test_perf_registry_lookup(capsys):
    name = "spotify_play_music"
    iters = 100_000
    t0 = time.perf_counter()
    for _ in range(iters):
        actions.resolve_handler(name)
    per_us = (time.perf_counter() - t0) * 1e6 / iters
    with capsys.disabled():
        print(f"\n  registry lookup: {per_us:.3f} µs (ceiling {LOOKUP_CEILING_US})")
    assert per_us < LOOKUP_CEILING_US


def test_perf_matcher_rebuild(capsys):
    iters = 50
    t0 = time.perf_counter()
    for _ in range(iters):
        matcher._rebuild()
    per_ms = (time.perf_counter() - t0) * 1000 / iters
    with capsys.disabled():
        print(f"\n  matcher rebuild: {per_ms:.2f} ms (ceiling {REBUILD_CEILING_MS})")
    assert per_ms < REBUILD_CEILING_MS
