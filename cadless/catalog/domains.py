"""Catalog domain registry: domains are data, not code (issue #20).

Every catalog domain (house, mechanical, ...) is one :class:`Domain` entry
declaring its UI label and group sort order, the units its step scripts are
authored in (from which the artifact export scale to millimetres is derived —
issue #18), and the eval metric set the runner applies. Consumers look domains
up here instead of branching on the key, so adding a domain is a single
``register_domain()`` call (or a new entry in the built-ins below) plus
content — no code edits anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Millimetre factor per supported authoring unit. Exporters/viewers assume
# model units are mm (glTF's mm->m divide, STL/STEP conventions), so baked
# artifacts of non-mm domains are scaled by this factor at export time.
MM_PER_UNIT = {"mm": 1.0, "m": 1000.0}

# Metric sets the eval runner understands. BASE applies everywhere; MESH
# metrics compare the generated mesh against the step's baked golden STL
# (mechanical-style scoring, including the per-step ``expected_bodies``
# body-count assertion, #40); IR metrics are static, computed from the
# authored ``ir.json`` (house-style wall/stair checks).
BASE_METRICS = frozenset({"volume", "bbox"})
MESH_METRICS = frozenset({"chamfer", "iou", "feature_count", "body_count"})
IR_METRICS = frozenset({"wall_alignment", "stair_compliance"})

# Unregistered keys (legacy ledger entries) sort after every registered domain.
_UNREGISTERED_SORT = float("inf")


@dataclass(frozen=True)
class Domain:
    """One catalog domain, described entirely as data."""

    key: str
    label: str
    authoring_units: str = "mm"  # units the step scripts are authored in
    sort_order: int = 100  # UI group order (lower sorts first)
    eval_metrics: frozenset[str] = field(default=BASE_METRICS)
    # Content dirname under settings.catalog_root (#46). Defaults to
    # "{key}-catalog"; override only where history diverged (mech-catalog).
    content_dir: str = ""

    def __post_init__(self) -> None:
        if self.authoring_units not in MM_PER_UNIT:
            raise ValueError(
                f"authoring_units must be one of {sorted(MM_PER_UNIT)}, "
                f"got {self.authoring_units!r}"
            )
        if not self.content_dir:
            object.__setattr__(self, "content_dir", f"{self.key}-catalog")

    @property
    def export_scale(self) -> float:
        """Factor from authoring units to millimetres, applied at export time."""
        return MM_PER_UNIT[self.authoring_units]


_REGISTRY: dict[str, Domain] = {}


def register_domain(domain: Domain, *, replace: bool = False) -> Domain:
    """Add a domain to the registry; refuses to clobber unless ``replace``."""
    if domain.key in _REGISTRY and not replace:
        raise ValueError(f"domain {domain.key!r} is already registered")
    _REGISTRY[domain.key] = domain
    return domain


def unregister_domain(key: str) -> None:
    """Remove a domain (tests registering synthetic domains clean up with this)."""
    _REGISTRY.pop(key, None)


def get_domain(key: str) -> Domain:
    """The registered domain for ``key``; raises ``ValueError`` if unknown."""
    try:
        return _REGISTRY[key]
    except KeyError:
        raise ValueError(
            f"unknown catalog domain {key!r}; registered: {', '.join(sorted(_REGISTRY))}"
        ) from None


def find_domain(key: str) -> Domain | None:
    """The registered domain for ``key``, or ``None`` if unknown."""
    return _REGISTRY.get(key)


def all_domains() -> list[Domain]:
    """All registered domains in UI order (sort_order, then key)."""
    return sorted(_REGISTRY.values(), key=lambda d: (d.sort_order, d.key))


def domain_label(key: str) -> str:
    """UI label for a domain key; capitalizes unregistered (legacy) keys."""
    domain = _REGISTRY.get(key)
    return domain.label if domain else key.capitalize()


def domain_sort_key(key: str) -> tuple[float, str]:
    """Sort key for UI grouping; unregistered keys sort last, then by key."""
    domain = _REGISTRY.get(key)
    return (domain.sort_order if domain else _UNREGISTERED_SORT, key)


# ----------------------------------------------------------------------------- #
# built-in domains
# ----------------------------------------------------------------------------- #

register_domain(
    Domain(
        key="house",
        label="House",
        authoring_units="m",
        sort_order=0,
        eval_metrics=BASE_METRICS | IR_METRICS,
    )
)
register_domain(
    Domain(
        key="mechanical",
        label="Mechanical",
        authoring_units="mm",
        sort_order=10,
        eval_metrics=BASE_METRICS | MESH_METRICS,
        content_dir="mech-catalog",
    )
)
register_domain(
    Domain(
        key="furniture",
        label="Furniture",
        authoring_units="mm",
        sort_order=20,
        eval_metrics=BASE_METRICS | MESH_METRICS,
    )
)
register_domain(
    Domain(
        key="fixture",
        label="Enclosures & Fixtures",
        authoring_units="mm",
        sort_order=30,
        eval_metrics=BASE_METRICS | MESH_METRICS,
    )
)
