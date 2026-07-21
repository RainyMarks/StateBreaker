"""Probe cloning: identity-bearing probes must exist for every session.

Normal group: a ``/members/alice`` probe is cloned for bob. Anomaly groups:
a single session changes nothing, and resource-id probes are left alone.
"""

from __future__ import annotations

from statebreaker.intelligence.probe_discovery import clone_probes_for_sessions
from statebreaker.models.capture import RequestTemplate
from statebreaker.models.state import StateProbe


def _probe(path: str, probe_id: str = "probe-1") -> StateProbe:
    return StateProbe(
        probe_id=probe_id,
        request_template=RequestTemplate(
            template_id=f"tpl-{probe_id}", method="GET", path_template=path
        ),
    )


def test_identity_probe_is_cloned_for_other_sessions() -> None:
    probes = [_probe("/members/alice")]
    expanded = clone_probes_for_sessions(probes, ["alice", "bob"])
    assert len(expanded) == 2
    clone = expanded[1]
    assert clone.probe_id == "probe-1@bob"
    assert clone.request_template.path_template == "/members/bob"
    assert clone.request_template.template_id == "tpl-probe-1@bob"


def test_resource_probe_without_identity_is_not_cloned() -> None:
    probes = [_probe("/invites/${slug}")]
    expanded = clone_probes_for_sessions(probes, ["alice", "bob"])
    assert expanded == probes


def test_single_session_disables_cloning() -> None:
    probes = [_probe("/members/alice")]
    assert clone_probes_for_sessions(probes, ["alice"]) == probes
