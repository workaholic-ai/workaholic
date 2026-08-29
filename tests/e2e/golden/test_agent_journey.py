"""Golden specification for distinct local Human and Agent identities."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from tests.golden import (
    GoldenCliInvocation,
    require_array,
    require_error,
    require_object,
    require_string,
    require_success,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from subprocess import CompletedProcess

    from tests.golden import GoldenJourneyRunner, JsonObject

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.golden,
]


# Keep the exit journey linear so every fresh process observes durable state.
def test_distinct_human_and_agents_enforce_identity_across_cli_processes(  # noqa: PLR0915
    golden_runner: GoldenJourneyRunner,
    tmp_path: Path,
) -> None:
    """A Human provisions two least-privilege Agents that execute independently."""
    instance_root = tmp_path / "phase-five-instance"
    with golden_runner.instance(
        backend="sqlite",
        project_key="ACME",
        remote=False,
        root=instance_root,
        subjects={
            "operator": "human",
            "agent-one": "agent",
            "agent-two": "agent",
        },
    ) as instance:
        workspace = instance_root / "workspace"
        human_environment = instance.environment_for("operator")
        agent_environments = (
            instance.environment_for("agent-one"),
            instance.environment_for("agent-two"),
        )

        human_identity = _json_success(
            golden_runner,
            ("auth", "whoami"),
            workspace=workspace,
            environment=human_environment,
            context="Human identity",
        )
        human_subject = require_object(
            human_identity["subject"], context="Human Subject"
        )
        human_token = require_object(human_identity["token"], context="Human Token")
        human_id = require_string(human_subject["id"], context="Human Subject ID")
        human_token_id = require_string(human_token["id"], context="Human Token ID")
        assert human_subject["kind"] == "human"
        assert human_subject["is_instance_admin"] is True

        agent_identities = tuple(
            _json_success(
                golden_runner,
                ("auth", "whoami"),
                workspace=workspace,
                environment=environment,
                context=f"Agent {index} identity",
            )
            for index, environment in enumerate(agent_environments, start=1)
        )
        agent_subjects = tuple(
            require_object(identity["subject"], context="Agent Subject")
            for identity in agent_identities
        )
        agent_tokens = tuple(
            require_object(identity["token"], context="Agent Token")
            for identity in agent_identities
        )
        discovered_agent_ids = tuple(
            require_string(subject["id"], context="Agent Subject ID")
            for subject in agent_subjects
        )
        assert len(discovered_agent_ids) == 2
        agent_ids = (discovered_agent_ids[0], discovered_agent_ids[1])
        assert agent_ids[0] != agent_ids[1] != human_id
        assert all(subject["kind"] == "agent" for subject in agent_subjects)
        assert all(subject["is_instance_admin"] is False for subject in agent_subjects)
        assert agent_tokens[0]["id"] != agent_tokens[1]["id"]

        _json_success(
            golden_runner,
            (
                "project",
                "create",
                "--key",
                "DOCS",
                "--name",
                "Documentation",
                "--idempotency-key",
                "golden-create-docs",
            ),
            workspace=workspace,
            environment=human_environment,
            context="DOCS Project",
        )
        _json_success(
            golden_runner,
            (
                "auth",
                "grant",
                "agent-two",
                "viewer",
                "--project",
                "DOCS",
                "--idempotency-key",
                "golden-docs-viewer",
            ),
            workspace=workspace,
            environment=human_environment,
            context="DOCS Viewer grant",
        )
        assert _project_keys(
            _json_success(
                golden_runner,
                ("project", "list"),
                workspace=workspace,
                environment=agent_environments[0],
                context="Agent one Projects",
            )
        ) == ["ACME"]
        assert _project_keys(
            _json_success(
                golden_runner,
                ("project", "list"),
                workspace=workspace,
                environment=agent_environments[1],
                context="Agent two Projects",
            )
        ) == ["ACME", "DOCS"]

        shared_task = _add_task(
            golden_runner,
            workspace=workspace,
            environment=human_environment,
            title="Shared authenticated delivery",
            idempotency_key="golden-shared-task",
        )
        docs_task = _add_task(
            golden_runner,
            workspace=workspace,
            environment=human_environment,
            title="Viewer-visible documentation",
            idempotency_key="golden-docs-task",
            project="DOCS",
        )
        assert shared_task["key"] == "ACME-1"
        assert docs_task["key"] == "DOCS-1"

        viewer_denial = golden_runner.cli(
            (
                "task",
                "claim",
                "--project",
                "DOCS",
                "--json",
                "--non-interactive",
            ),
            cwd=workspace,
            environment=agent_environments[1],
        )
        require_error(viewer_denial, expected_code="PERMISSION_DENIED")

        race_results = golden_runner.cli_race(
            tuple(
                GoldenCliInvocation(
                    arguments=(
                        "task",
                        "claim",
                        "--project",
                        "ACME",
                        "--lease",
                        "15m",
                        "--idempotency-key",
                        f"golden-race-{index}",
                        "--json",
                        "--non-interactive",
                    ),
                    cwd=workspace,
                    environment=environment,
                )
                for index, environment in enumerate(agent_environments, start=1)
            )
        )
        winner_index, claim = _assert_exclusive_agent_race(
            race_results, agent_ids=agent_ids
        )
        loser_index = 1 - winner_index
        winner_environment = agent_environments[winner_index]
        loser_environment = agent_environments[loser_index]
        winner_id = agent_ids[winner_index]
        loser_id = agent_ids[loser_index]
        claim_record = require_object(claim["claim"], context="winning Claim")
        attempt = require_object(claim["attempt"], context="winning Attempt")
        attempt_id = require_string(attempt["id"], context="winning Attempt ID")
        assert claim_record["subject_id"] == winner_id
        assert claim_record["attempt_id"] == attempt_id
        assert attempt["subject_id"] == winner_id

        foreign_progress = golden_runner.cli(
            (
                "task",
                "progress",
                "ACME-1",
                "--attempt",
                attempt_id,
                "--input-file",
                "-",
                "--json",
                "--non-interactive",
            ),
            cwd=workspace,
            environment=loser_environment,
            input_text=json.dumps({"message": "Foreign writer must fail."}),
        )
        require_error(foreign_progress, expected_code="LEASE_LOST")

        heartbeat = _json_success(
            golden_runner,
            (
                "task",
                "heartbeat",
                "ACME-1",
                "--attempt",
                attempt_id,
                "--lease",
                "30m",
                "--idempotency-key",
                "golden-heartbeat",
            ),
            workspace=workspace,
            environment=winner_environment,
            context="Agent heartbeat",
        )
        _assert_events(
            heartbeat,
            expected_types=("claim_renewed",),
            subject_id=winner_id,
            attempt_id=attempt_id,
        )
        progress = _json_success(
            golden_runner,
            (
                "task",
                "progress",
                "ACME-1",
                "--attempt",
                attempt_id,
                "--input-file",
                "-",
                "--idempotency-key",
                "golden-progress",
            ),
            workspace=workspace,
            environment=winner_environment,
            input_text=json.dumps(
                {
                    "message": "Implementing with an authenticated Agent.",
                    "percent_complete": 75,
                },
                separators=(",", ":"),
            ),
            context="Agent progress",
        )
        _assert_events(
            progress,
            expected_types=("progress_reported",),
            subject_id=winner_id,
            attempt_id=attempt_id,
        )

        alternate_path = instance.token_file_for("winner-alternate")
        alternate = _json_success(
            golden_runner,
            (
                "auth",
                "create-token",
                winner_id,
                "--token-file",
                str(alternate_path),
                "--idempotency-key",
                "golden-winner-alternate",
            ),
            workspace=workspace,
            environment=human_environment,
            context="alternate Agent Token",
        )
        alternate_token = require_string(alternate["id"], context="alternate Token ID")
        alternate_environment = {"WORKAHOLIC_TOKEN_FILE": str(alternate_path)}
        assert alternate_path.stat().st_mode & 0o777 == 0o600
        alternate_identity = _json_success(
            golden_runner,
            ("auth", "whoami"),
            workspace=workspace,
            environment=alternate_environment,
            context="alternate Token identity",
        )
        assert (
            require_object(alternate_identity["subject"], context="alternate Subject")[
                "id"
            ]
            == winner_id
        )
        assert (
            require_object(alternate_identity["token"], context="alternate Token")["id"]
            == alternate_token
        )

        original_winner_token = require_string(
            agent_tokens[winner_index]["id"], context="original winner Token ID"
        )
        _json_success(
            golden_runner,
            (
                "auth",
                "revoke-token",
                original_winner_token,
                "--idempotency-key",
                "golden-revoke-winner-token",
            ),
            workspace=workspace,
            environment=human_environment,
            context="winner Token revocation",
        )
        require_error(
            golden_runner.cli(
                ("auth", "whoami", "--json", "--non-interactive"),
                cwd=workspace,
                environment=winner_environment,
            ),
            expected_code="AUTHENTICATION_FAILED",
        )
        continuity = _json_success(
            golden_runner,
            (
                "task",
                "heartbeat",
                "ACME-1",
                "--attempt",
                attempt_id,
                "--lease",
                "45m",
                "--idempotency-key",
                "golden-alternate-heartbeat",
            ),
            workspace=workspace,
            environment=alternate_environment,
            context="same-Subject Token continuity",
        )
        assert (
            require_object(continuity["claim"], context="continued Claim")["attempt_id"]
            == attempt_id
        )

        submission = _json_success(
            golden_runner,
            (
                "task",
                "submit",
                "ACME-1",
                "--attempt",
                attempt_id,
                "--expected-version",
                "1",
                "--result-file",
                "-",
                "--idempotency-key",
                "golden-agent-submit",
            ),
            workspace=workspace,
            environment=alternate_environment,
            input_text=_result_payload("Authenticated Agent delivery complete."),
            context="Agent submission",
        )
        assert (
            require_object(submission["task"], context="submitted Task")["state"]
            == "done"
        )
        assert (
            require_object(submission["result"], context="Agent Result")["submitted_by"]
            == winner_id
        )
        assert submission["claim"] is None

        disabled_task = _add_task(
            golden_runner,
            workspace=workspace,
            environment=human_environment,
            title="Claim survives Subject disablement",
            idempotency_key="golden-disable-task",
        )
        disabled_claim = _json_success(
            golden_runner,
            (
                "task",
                "claim",
                "--project",
                "ACME",
                "--lease",
                "15m",
                "--idempotency-key",
                "golden-disable-claim",
            ),
            workspace=workspace,
            environment=loser_environment,
            context="soon-disabled Agent Claim",
        )
        assert disabled_task["key"] == "ACME-2"
        assert (
            require_object(disabled_claim["claim"], context="soon-disabled Claim")[
                "subject_id"
            ]
            == loser_id
        )
        _json_success(
            golden_runner,
            (
                "auth",
                "disable-subject",
                loser_id,
                "--expected-version",
                "1",
                "--idempotency-key",
                "golden-disable-loser",
            ),
            workspace=workspace,
            environment=human_environment,
            context="losing Agent disablement",
        )
        require_error(
            golden_runner.cli(
                ("auth", "whoami", "--json", "--non-interactive"),
                cwd=workspace,
                environment=loser_environment,
            ),
            expected_code="AUTHENTICATION_FAILED",
        )
        retained = _json_success(
            golden_runner,
            ("task", "show", "ACME-2"),
            workspace=workspace,
            environment=human_environment,
            context="retained disabled-Subject Claim",
        )
        retained_task = require_object(retained["task"], context="retained Task")
        retained_views = require_object(
            retained_task["views"], context="retained Task views"
        )
        assert retained_views["running"] is True
        assert retained_views["ready"] is False

        history = _json_success(
            golden_runner,
            ("task", "events", "ACME-1", "--limit", "100"),
            workspace=workspace,
            environment=human_environment,
            context="authenticated Task history",
        )
        history_events = [
            require_object(item, context="TaskEvent")
            for item in require_array(history["events"], context="TaskEvents")
        ]
        assert history_events[0]["actor_subject_id"] == human_id
        assert history_events[0]["actor_kind"] == "human"
        assert all(
            event["actor_subject_id"] == winner_id and event["actor_kind"] == "agent"
            for event in history_events[1:]
        )

        audit = _json_success(
            golden_runner,
            ("auth", "events", "--limit", "100"),
            workspace=workspace,
            environment=human_environment,
            context="administrative audit",
        )
        audit_events = [
            require_object(item, context="AuditEvent")
            for item in require_array(audit["events"], context="AuditEvents")
        ]
        assert audit_events[0]["event_type"] == "instance_bootstrapped"
        assert audit_events[0]["actor_subject_id"] == human_id
        assert all(event["actor_subject_id"] == human_id for event in audit_events)
        tokenless_bootstrap = audit_events[:2]
        assert [event["event_type"] for event in tokenless_bootstrap] == [
            "instance_bootstrapped",
            "token_issued",
        ]
        assert all(event["actor_token_id"] is None for event in tokenless_bootstrap)
        assert all(
            event["actor_token_id"] == human_token_id for event in audit_events[2:]
        )
        event_types = {event["event_type"] for event in audit_events}
        assert {
            "project_created",
            "subject_created",
            "project_grant_assigned",
            "token_issued",
            "token_revoked",
            "subject_disabled",
        }.issubset(event_types)
        _assert_secret_free(history)
        _assert_secret_free(audit)


def _json_success(  # noqa: PLR0913 - complete fresh-process boundary.
    runner: GoldenJourneyRunner,
    arguments: Sequence[str],
    *,
    workspace: Path,
    context: str,
    environment: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> JsonObject:
    """Run one fresh JSON CLI process and require an object payload."""
    result = runner.cli(
        (*arguments, "--json", "--non-interactive"),
        cwd=workspace,
        environment=environment,
        input_text=input_text,
    )
    return require_object(require_success(result), context=context)


def _add_task(  # noqa: PLR0913 - explicit task journey inputs.
    runner: GoldenJourneyRunner,
    *,
    workspace: Path,
    environment: Mapping[str, str],
    title: str,
    idempotency_key: str,
    project: str | None = None,
) -> JsonObject:
    """Create and return one Task through a fresh public CLI process."""
    project_arguments = () if project is None else ("--project", project)
    data = _json_success(
        runner,
        (
            "task",
            "add",
            title,
            *project_arguments,
            "--idempotency-key",
            idempotency_key,
        ),
        workspace=workspace,
        environment=environment,
        context=f"{title} creation",
    )
    assert data.keys() == {"task"}
    return require_object(data["task"], context=title)


def _project_keys(data: JsonObject) -> list[str]:
    """Return stable visible Project keys from one list result."""
    return [
        require_string(
            require_object(item, context="visible Project")["key"],
            context="visible Project key",
        )
        for item in require_array(data["projects"], context="visible Projects")
    ]


def _result_payload(summary: str) -> str:
    """Serialize one deterministic closed Result input."""
    return json.dumps(
        {
            "summary": summary,
            "criteria": [],
            "artifacts": [],
            "proposed_follow_ups": [],
        },
        separators=(",", ":"),
    )


def _assert_events(
    data: JsonObject,
    *,
    expected_types: tuple[str, ...],
    subject_id: str,
    attempt_id: str,
) -> list[JsonObject]:
    """Validate one attributable ordered Agent TaskEvent batch."""
    events = [
        require_object(item, context="Agent TaskEvent")
        for item in require_array(data["events"], context="Agent events")
    ]
    assert [event["type"] for event in events] == list(expected_types)
    assert all(event["actor_subject_id"] == subject_id for event in events)
    assert all(event["actor_kind"] == "agent" for event in events)
    assert all(event["attempt_id"] == attempt_id for event in events)
    return events


def _assert_exclusive_agent_race(
    results: tuple[CompletedProcess[str], ...],
    *,
    agent_ids: tuple[str, str],
) -> tuple[int, JsonObject]:
    """Require exactly one distinct Agent winner and one documented loser."""
    winners = [index for index, result in enumerate(results) if result.returncode == 0]
    assert len(winners) == 1
    winner_index = winners[0]
    winner = require_object(
        require_success(results[winner_index]), context="Agent Claim race winner"
    )
    winner_task = require_object(winner["task"], context="race winner Task")
    winner_attempt = require_object(winner["attempt"], context="race winner Attempt")
    assert winner_task["key"] == "ACME-1"
    assert winner_attempt["subject_id"] == agent_ids[winner_index]
    loser = require_error(results[1 - winner_index], expected_code="NO_TASK_AVAILABLE")
    assert loser["retryable"] is True
    return winner_index, winner


def _assert_secret_free(data: JsonObject) -> None:
    """Reject credential-bearing field names from a complete public payload."""
    encoded = json.dumps(data, sort_keys=True)
    for forbidden in (
        "raw_token",
        "token_hash",
        "credential_path",
        "WORKAHOLIC_TOKEN_FILE",
    ):
        assert forbidden not in encoded
