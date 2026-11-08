"""OP-404: the per-profile weight and matrix cache behind ``Router``.

The cache must never change a route: every test here compares cached results with
the plain functions (``profile_weights`` and ``length_matrix``) or with a router
that rebuilds everything on every call.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from cloud.app.network import (
    DEFAULT_NETWORK_PATH,
    RawEdge,
    RawNode,
    WalkNetwork,
    export_network,
)
from cloud.app.profiles import load_routing_config
from cloud.app.routing import (
    NO_HAZARDS,
    EdgeHazard,
    HazardMap,
    ProfileWeights,
    Router,
    WeightCache,
    profile_weights,
)
from cloud.app.schemas import Confidence, Effect

# A 3 x 3 grid, 100 m apart, with one staircase (node 2 to node 5), as in
# test_routing.py. Node ids are 1..9, row by row.
LAT0, LNG0 = 53.5440, -113.4900
DLAT, DLNG = 0.0009, 0.0015


def grid_network() -> tuple[WalkNetwork, dict[tuple[int, int], int]]:
    nodes = [
        RawNode(osmid=i + 1, lat=LAT0 + (i // 3) * DLAT, lng=LNG0 + (i % 3) * DLNG)
        for i in range(9)
    ]
    edges: list[RawEdge] = []
    for r in range(3):
        for c in range(3):
            n = r * 3 + c + 1
            if c < 2:
                edges.append(
                    RawEdge(n, n + 1, 100.0, highway="footway", name=f"Avenue {r}")
                )
            if r < 2:
                highway = "steps" if (n, n + 3) == (2, 5) else "footway"
                edges.append(
                    RawEdge(n, n + 3, 100.0, highway=highway, name=f"Street {c}")
                )
    net = WalkNetwork.from_data(export_network(nodes, edges, source="test"))
    index = {
        (int(net.edge_u[i]) + 1, int(net.edge_v[i]) + 1): i
        for i in range(net.edge_count)
    }
    return net, index


def latlng(node: int) -> tuple[float, float]:
    i = node - 1
    return LAT0 + (i // 3) * DLAT, LNG0 + (i % 3) * DLNG


def hazard(item_id: str, effect: Effect) -> EdgeHazard:
    return EdgeHazard(item_id, effect, Confidence.HIGH, item_id)


class RebuildEverything(WeightCache):
    """A cache that never remembers: the uncached reference."""

    def get(self, profile, hazards):  # type: ignore[override]
        return ProfileWeights.build(self.network, profile, hazards)


@pytest.fixture
def grid():
    return grid_network()


@pytest.fixture
def config():
    return load_routing_config()


# ------------------------------------------------------------------ the cache


def test_a_hit_returns_the_same_entry(grid, config) -> None:
    net, index = grid
    cache = WeightCache(net)
    standard = config.profile("standard")
    hazards: HazardMap = {index[(4, 5)]: [hazard("c:1", Effect.BLOCKED)]}
    first = cache.get(standard, hazards)
    assert cache.get(standard, hazards) is first
    assert (cache.hits, cache.misses) == (1, 1)
    assert np.array_equal(first.weights, profile_weights(net, standard, hazards))
    # Another profile on the same map is another entry.
    assert cache.get(config.profile("wheelchair"), hazards) is not first
    assert cache.size == 2


def test_a_new_map_object_misses_and_replaces_the_old_one(grid, config) -> None:
    """The providers build a new map on every change: that is what clears the cache."""
    net, index = grid
    cache = WeightCache(net, max_maps=1)
    standard = config.profile("standard")
    old: HazardMap = {index[(4, 5)]: [hazard("c:1", Effect.BLOCKED)]}
    new: HazardMap = {index[(4, 5)]: [hazard("c:1", Effect.BLOCKED)]}  # equal content
    first = cache.get(standard, old)
    second = cache.get(standard, new)
    assert second is not first and np.array_equal(second.weights, first.weights)
    assert cache.size == 1  # the old map's entries are gone
    assert cache.get(standard, old) is not first  # and a request for it misses
    assert cache.misses == 3 and cache.hits == 0


def test_empty_maps_share_the_plain_entries(grid, config) -> None:
    net, _ = grid
    cache = WeightCache(net)
    standard = config.profile("standard")
    plain = cache.get(standard, NO_HAZARDS)
    assert cache.get(standard, {}) is plain
    assert cache.get(standard, {}) is plain  # a fresh {} each time
    assert cache.size == 1 and cache.hits == 2


def test_cached_arrays_are_read_only(grid, config) -> None:
    net, _ = grid
    entry = WeightCache(net).get(config.profile("standard"), NO_HAZARDS)
    with pytest.raises(ValueError, match="read-only"):
        entry.weights[0] = 1.0
    with pytest.raises(ValueError, match="read-only"):
        entry.indices[0] = 0


def test_clear_forgets_everything(grid, config) -> None:
    net, index = grid
    cache = WeightCache(net)
    cache.get(config.profile("standard"), NO_HAZARDS)
    cache.get(config.profile("standard"), {index[(4, 5)]: [hazard("c", Effect.SNOW)]})
    assert cache.size == 2
    cache.clear()
    assert cache.size == 0


# ------------------------------------------------------------------ the matrix


def test_matrix_equals_length_matrix(grid, config) -> None:
    net, index = grid
    wheelchair = config.profile("wheelchair")  # removes the stairs
    hazards: HazardMap = {index[(4, 5)]: [hazard("c:1", Effect.BLOCKED)]}
    entry = ProfileWeights.build(net, wheelchair, hazards)
    expected = net.length_matrix(entry.weights)
    got = entry.matrix()
    assert got.shape == expected.shape and got.nnz == expected.nnz
    assert np.array_equal(got.data, expected.data)
    assert np.array_equal(got.indices, expected.indices)
    assert np.array_equal(got.indptr, expected.indptr)


def test_matrix_for_penalised_weights_reuses_the_structure(grid, config) -> None:
    net, index = grid
    entry = ProfileWeights.build(net, config.profile("wheelchair"), NO_HAZARDS)
    penalised = entry.weights.copy()
    penalised[[index[(1, 2)], index[(2, 3)]]] *= 1.6
    expected = net.length_matrix(penalised)
    got = entry.matrix(penalised)
    assert got.indptr is entry.indptr  # the cached structure, not a rebuild
    assert np.array_equal(got.data, expected.data)
    assert np.array_equal(got.indices, expected.indices)
    assert np.array_equal(got.indptr, expected.indptr)


def test_matrix_for_other_weights_falls_back_to_a_full_build(grid, config) -> None:
    """Weights that remove an extra edge, or restore one, have another zero pattern."""
    net, index = grid
    entry = ProfileWeights.build(net, config.profile("standard"), NO_HAZARDS)
    fewer = entry.weights.copy()
    fewer[index[(1, 2)]] = 0.0
    more = ProfileWeights.build(net, config.profile("wheelchair"), NO_HAZARDS)
    restored = more.weights.copy()
    restored[index[(2, 5)]] = 100.0  # the stairs are back
    for weights, base in ((fewer, entry), (restored, more)):
        expected = net.length_matrix(weights)
        got = base.matrix(weights)
        assert got.nnz == expected.nnz
        assert np.array_equal(got.data, expected.data)
        assert np.array_equal(got.indices, expected.indices)
        assert np.array_equal(got.indptr, expected.indptr)


# ------------------------------------------------------------------ the router


def routes_agree(cached: Router, reference: Router, trips, profiles, hazards) -> None:
    for a, b in trips:
        for profile in profiles:
            got = cached.route(*a, *b, profile, hazards)
            expected = reference.route(*a, *b, profile, hazards)
            assert got == expected, (a, b, profile)


def test_grid_routes_are_identical_with_and_without_the_cache(grid, config) -> None:
    net, index = grid
    cached = Router(net, config)
    reference = Router(net, config, cache=RebuildEverything(net))
    maps: list[HazardMap] = [
        {},
        {index[(4, 5)]: [hazard("c:1", Effect.BLOCKED)]},
        {
            index[(5, 6)]: [hazard("s:1", Effect.SNOW)],
            index[(1, 4)]: [hazard("i", Effect.ICE_RISK)],
        },
        {index[(2, 3)]: [hazard("w", Effect.CAUTION)]},  # changes no weight
    ]
    trips = [(latlng(1), latlng(9)), (latlng(2), latlng(8)), (latlng(7), latlng(3))]
    for hazards in maps:
        routes_agree(cached, reference, trips, config.profiles, hazards)
    assert cached.cache.hits > 0


def test_caution_only_hazards_take_the_plain_route_shortcut(grid, config) -> None:
    """When no hazard changes a weight, the best route is the plain route, and the
    result is the same as with no hazards at all."""
    net, _ = grid
    router = Router(net, config)
    without = router.route(*latlng(1), *latlng(9), "standard", {})
    on_best = without.routes[0].edges[1]  # a caution on the best route itself
    caution: HazardMap = {on_best: [hazard("w", Effect.CAUTION)]}
    with_caution = router.route(*latlng(1), *latlng(9), "standard", caution)
    assert with_caution.plain_nodes == with_caution.routes[0].nodes
    assert with_caution.plain_nodes == without.plain_nodes
    assert [r.nodes for r in with_caution.routes] == [r.nodes for r in without.routes]
    assert [h.item_id for h in with_caution.routes[0].hazards_on_route] == ["w"]
    assert (
        with_caution.routes[0].hazards_avoided == []
        and without.routes[0].reason is None
    )


def test_a_second_router_shares_the_cache(grid, config) -> None:
    net, _ = grid
    first = Router(net, config)
    one_route = config.model_copy(
        update={"alternates": config.alternates.model_copy(update={"max_routes": 1})}
    )
    second = Router(net, one_route, cache=first.cache)
    first.route(*latlng(1), *latlng(9), "standard", {})
    second.route(*latlng(1), *latlng(9), "standard", {})
    assert first.cache is second.cache and first.cache.hits >= 1


def test_a_cache_from_another_network_is_refused(grid, config) -> None:
    net, _ = grid
    other, _ = grid_network()
    with pytest.raises(ValueError, match="another network"):
        Router(net, config, cache=WeightCache(other))


def test_many_threads_get_correct_entries(grid, config) -> None:
    """Thread-safety smoke test: 16 threads, two maps and every profile at once."""
    net, index = grid
    cache = WeightCache(net, max_maps=2)
    maps: list[HazardMap] = [
        {index[(4, 5)]: [hazard("c:1", Effect.BLOCKED)]},
        {index[(5, 6)]: [hazard("s:1", Effect.SNOW)]},
    ]
    profiles = list(config.profiles.values())

    def work(seed: int) -> bool:
        ok = True
        for i in range(40):
            profile = profiles[(seed + i) % len(profiles)]
            hazards = maps[(seed * 7 + i) % 2]
            entry = cache.get(profile, hazards)
            ok &= np.array_equal(entry.weights, profile_weights(net, profile, hazards))
            ok &= not entry.weights.flags.writeable
        return ok

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(work, range(16)))
    assert all(results)
    assert cache.size <= 2 * len(profiles)
    assert cache.hits + cache.misses == 16 * 40


# ------------------------------------------------------------- the real city


@pytest.mark.skipif(
    not DEFAULT_NETWORK_PATH.exists(), reason="cloud/data/network.npz not built"
)
def test_city_routes_are_identical_with_and_without_the_cache(config) -> None:
    net = WalkNetwork.load()
    cached = Router(net, config)
    reference = Router(net, config, cache=RebuildEverything(net))
    churchill, rogers = (53.5444, -113.4909), (53.5468, -113.4973)
    university, whyte = (53.5232, -113.5263), (53.5186, -113.4950)
    legislature = (53.5335, -113.5064)
    trips = [(churchill, rogers), (university, whyte), (legislature, university)]
    # Close the edge from the OP-206 review plus some snow and ice near downtown.
    rng = np.random.default_rng(404)
    hazards: dict[int, list[EdgeHazard]] = {
        4562: [hazard("test:closure", Effect.BLOCKED)]
    }
    for edge in rng.choice(net.edge_count, 300, replace=False):
        hazards.setdefault(int(edge), []).append(hazard(f"snow:{edge}", Effect.SNOW))
    for edge in rng.choice(net.edge_count, 300, replace=False):
        hazards.setdefault(int(edge), []).append(hazard(f"ice:{edge}", Effect.ICE_RISK))
    for map_ in ({}, hazards):
        routes_agree(cached, reference, trips, config.profiles, map_)
    assert cached.cache.size == 2 * len(config.profiles)
