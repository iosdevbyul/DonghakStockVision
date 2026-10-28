"""Explicit local capture operations. No network without capture --allow-network."""

import argparse
import hashlib
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.historical_readiness import HistoricalReadScope
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan
from donghak_stock_vision.data.research_split import ExplicitResearchSplit, ResearchDateRange
from donghak_stock_vision.storage.forward_capture import (
    DB,
    assess_capture_pit,
    capture_status,
    readback,
    run_capture,
    verify_capture,
)
from donghak_stock_vision.trading.domain import Currency, Exchange, Instrument


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser(
        "forward-capture", help="explicit bounded forward capture; offline by default"
    )
    cmd.add_argument(
        "action",
        choices=(
            "dry-run",
            "authorization-readiness",
            "recover",
            "inspect-recovery",
            "capture",
            "status",
            "verify",
            "readback",
            "history",
            "offline-pilot",
            "custody-readiness",
            "custody-stage",
            "custody-status",
            "custody-verify",
            "custody-readback",
        ),
    )
    cmd.add_argument("--root", type=Path, required=True)
    cmd.add_argument("--plan", type=Path)
    cmd.add_argument("--plan-pin")
    cmd.add_argument("--attempt", default="pilot-1")
    cmd.add_argument("--pin")
    cmd.add_argument("--sequence", type=int, default=0)
    cmd.add_argument("--output", type=Path)
    cmd.add_argument("--recovery", type=Path)
    cmd.add_argument("--recovery-pin")
    cmd.add_argument("--allow-network", action="store_true")
    cmd.add_argument("--authorization-root", type=Path)
    cmd.add_argument("--authorization-pin")
    cmd.add_argument(
        "--authorization-policy",
        choices=("independent_review_v1", "operator_read_only_pilot_v1"),
        default="independent_review_v1",
    )
    cmd.add_argument("--confirm-operator-authorization", action="store_true")
    from donghak_stock_vision.storage.krx_authorization import add_trust_arguments

    add_trust_arguments(cmd)
    for name in ("operations-config", "binding", "acknowledgement", "returned-readback"):
        cmd.add_argument(f"--{name}", type=Path)
        cmd.add_argument(f"--{name}-pin")


def offline_plan() -> ForwardCapturePlan:
    split = ExplicitResearchSplit(
        ResearchDateRange(date(2025, 7, 1), date(2025, 7, 3)),
        ResearchDateRange(date(2025, 7, 4), date(2025, 7, 4)),
        ResearchDateRange(date(2025, 7, 7), date(2025, 7, 7)),
    )
    return ForwardCapturePlan(
        "KOSPI",
        "005930",
        date(2025, 7, 1),
        "training",
        split,
        split.identifier,
        1,
        65536,
        30,
        1,
        10,
        "offline_fixture",
        None,
        None,
    )


def fixture_transport(plan: ForwardCapturePlan) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Date": "Wed, 02 Jul 2025 00:00:00 GMT"},
            content=json.dumps(
                {
                    "OutBlock_1": [
                        {
                            "ISU_CD": plan.ticker,
                            "BAS_DD": plan.day.strftime("%Y%m%d"),
                            "MKT_NM": plan.market,
                            "TDD_OPNPRC": "100",
                            "TDD_HGPRC": "110",
                            "TDD_LWPRC": "90",
                            "TDD_CLSPRC": "105",
                            "ACC_TRDVOL": "10",
                            "ACC_TRDVAL": "1050",
                        }
                    ]
                }
            ).encode(),
        )

    return httpx.MockTransport(handler)


def run(args: argparse.Namespace) -> int:
    if args.action == "status":
        print(capture_status(args.root).payload_json)
        return 0
    if args.action == "offline-pilot":
        from importlib.util import find_spec

        if find_spec("pydantic") is None:
            raise ValueError("offline_pit_assessment_requires_existing_api_extra")
        plan = offline_plan()
    else:
        if args.plan is None or args.plan_pin is None or args.plan.stat().st_size > 32768:
            raise ValueError("bounded_plan_and_independent_plan_pin_required")
        owner = FrozenJSON(args.plan.read_text())
        if owner.identifier != args.plan_pin:
            raise ValueError("plan_pin_mismatch")
        plan = ForwardCapturePlan.from_dict(owner.to_dict())
    if args.action.startswith("custody-"):
        from donghak_stock_vision.forward_custody_cli import run as run_custody

        return run_custody(args, plan)
    from donghak_stock_vision.storage.krx_authorization import (
        cli_trust,
        operator_readiness,
        readiness,
    )

    trust = cli_trust(args)
    operator_policy = args.authorization_policy == "operator_read_only_pilot_v1"
    if args.confirm_operator_authorization and not operator_policy:
        raise ValueError("operator_confirmation_requires_explicit_operator_policy")
    assess = operator_readiness if operator_policy else readiness
    authorization = None
    if args.authorization_root is not None or args.authorization_pin is not None:
        if args.authorization_root is None or args.authorization_pin is None:
            raise ValueError("explicit_authorization_root_and_pin_required")
        authorization = FrozenJSON.freeze(
            {"root": str(args.authorization_root), "grant_pin": args.authorization_pin}
        )
    if args.action == "inspect-recovery":
        from donghak_stock_vision.storage.krx_capture_recovery import inspect_recovery

        if (
            not operator_policy
            or authorization is None
            or trust is not None
            or args.pin is None
            or args.recovery is None
            or args.recovery_pin is None
            or args.output is not None
            or args.allow_network
            or args.confirm_operator_authorization
        ):
            raise ValueError("offline_inspection_requires_recovery_and_original_trusted_pins")
        with args.recovery.open("rb") as stream:
            raw = stream.read(32769)
        if len(raw) > 32768:
            raise ValueError("recovery_artifact_byte_limit")
        try:
            artifact = FrozenJSON(raw.decode("utf-8"))
        except ValueError:
            raise ValueError("invalid_recovery_artifact") from None
        print(
            inspect_recovery(
                artifact,
                args.recovery_pin,
                args.root,
                plan,
                args.attempt,
                args.pin,
                authorization,
            ).payload_json
        )
        return 0
    if args.action == "recover":
        from donghak_stock_vision.storage.krx_capture_recovery import recover_capture

        if (
            not operator_policy
            or authorization is None
            or args.authorization_root is None
            or trust is not None
            or args.pin is None
            or args.output is None
            or args.allow_network
            or args.confirm_operator_authorization
        ):
            raise ValueError("offline_recovery_requires_operator_policy_and_original_pins")
        output = args.output
        if (
            not output.is_absolute()
            or output.resolve() != output
            or not output.parent.is_dir()
            or output.parent.stat().st_mode & 0o077
            or output.is_relative_to(args.root.resolve())
            or output.is_relative_to(args.authorization_root.resolve())
        ):
            raise ValueError("separate_private_recovery_output_required")
        artifact = recover_capture(args.root, plan, args.attempt, args.pin, authorization)
        try:
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise ValueError("recovery_output_already_exists") from None
        with os.fdopen(fd, "w") as stream:
            stream.write(artifact.payload_json)
            stream.flush()
            os.fsync(stream.fileno())
        print(
            FrozenJSON.freeze(
                {
                    "version": "krx_capture_recovery_result_v1",
                    "recovery_pin": artifact.identifier,
                    "derived_observations": len(artifact.to_dict()["observations"]),
                    "market_row_count": artifact.to_dict()["market_row_count"],
                    "original_capture_changed": False,
                    "network_requests": 0,
                }
            ).payload_json
        )
        return 0
    if args.action == "authorization-readiness" or (
        args.action == "dry-run" and plan.mode == "authorized_provider"
    ):
        print(
            assess(
                plan,
                args.attempt,
                authorization,
                trust,
                datetime.now(UTC),
                bool(os.environ.get("KRX_API_KEY", "").strip()),
                credential=os.environ.get("KRX_API_KEY"),
                capture_root=args.root,
            ).payload_json
        )
        return 0
    if args.action == "dry-run":
        print(
            FrozenJSON.freeze(
                {
                    "version": "forward_capture_readiness_v1",
                    "plan": plan.document.to_dict(),
                    "plan_pin": plan.document.identifier,
                    "blockers": plan.blockers(bool(os.environ.get("KRX_API_KEY", "").strip())),
                    "network_requests": 0,
                    "pit_qualified": False,
                }
            ).payload_json
        )
        return 0
    if args.action in ("verify", "readback", "history"):
        if not args.pin:
            raise ValueError("independently_retained_capture_pin_required")
        owner = verify_capture(args.root, plan, args.attempt, args.pin)
        if args.action == "history":
            doc = owner.to_dict()
            print(
                FrozenJSON.freeze(
                    {
                        "capture_id": doc["capture_id"],
                        "previous_captures": doc["previous_captures"],
                        "responses": [
                            {
                                "raw_id": r["raw_id"],
                                "raw_sha256": r["receipt"]["rawSha256"],
                                "retrieved_at": r["receipt"]["collectedAt"],
                                "complete": r["metadata"]["complete"],
                            }
                            for r in doc["responses"]
                        ],
                        "assurance": "local_integrity_not_authenticated_custody",
                    }
                ).payload_json
            )
        elif args.action == "readback":
            if args.output is None:
                raise ValueError("private_output_path_required")
            body = readback(args.root, plan, args.attempt, args.pin, args.sequence)
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            print(json.dumps({"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}))
        else:
            print(owner.payload_json)
        return 0
    if plan.mode == "authorized_provider":
        assessed = assess(
            plan,
            args.attempt,
            authorization,
            trust,
            datetime.now(UTC),
            bool(os.environ.get("KRX_API_KEY", "").strip()),
            credential=os.environ.get("KRX_API_KEY"),
            capture_root=args.root,
        ).to_dict()
        if assessed["blockers"]:
            raise ValueError(",".join(assessed["blockers"]))
        if not args.allow_network:
            raise ValueError("explicit_network_permission_required")
        if operator_policy and not args.confirm_operator_authorization:
            raise ValueError("explicit_operator_authorization_confirmation_required")
        if assessed["origin"] != "provider_https":
            raise ValueError("mock_review_is_test_only_no_cli_provider_request")
    transport = fixture_transport(plan) if plan.mode == "offline_fixture" else None
    with httpx.Client(
        transport=transport, trust_env=False, follow_redirects=False, timeout=20
    ) as client:
        kwargs: dict[str, Any] = {}
        if args.action == "offline-pilot":
            kwargs["clock"] = lambda: datetime(2025, 7, 2, tzinfo=UTC)
        result = run_capture(
            args.root,
            plan,
            args.attempt,
            client,
            trusted_resume_pin=args.pin,
            api_key=os.environ.get("KRX_API_KEY", "") if plan.mode == "authorized_provider" else "",
            allow_network=args.allow_network,
            authorization=authorization,
            authorization_trust=trust,
            authorization_policy=args.authorization_policy,
            operator_confirmed=args.confirm_operator_authorization,
            **kwargs,
        )
    assessment = None
    if args.action == "offline-pilot":
        assert plan.split is not None and plan.split_pin is not None
        with (args.root / DB).open("rb") as stream:
            database_pin = hashlib.file_digest(stream, "sha256").hexdigest()
        scope = HistoricalReadScope(
            Instrument(plan.ticker, Exchange.KOSPI, Currency.KRW),
            ResearchDateRange(plan.day, plan.day),
            plan.partition,
            plan.split,
            plan.split_pin,
            database_pin,
        )
        assessment = assess_capture_pit(
            args.root, plan, args.attempt, result.identifier, scope, scope.document.identifier
        ).report.to_dict()
    extra: dict[str, Any] = {}
    if plan.mode == "authorized_provider":
        from donghak_stock_vision.storage.forward_capture import (
            verify_capture_authorization,
            verify_operator_capture_authorization,
        )

        assert authorization is not None
        if operator_policy:
            extra = {
                "version": "operator_read_only_capture_result_v1",
                "authorization_policy": args.authorization_policy,
                "independent_authorization_verified": False,
                "forward_signal_eligible": False,
                "paper_execution_eligible": False,
                "verified_origin": verify_operator_capture_authorization(
                    args.root,
                    plan,
                    args.attempt,
                    result.identifier,
                    authorization,
                    datetime.now(UTC),
                ),
            }
        else:
            extra = {
                "version": "authorized_forward_capture_result_v1",
                "verified_origin": verify_capture_authorization(
                    args.root,
                    plan,
                    args.attempt,
                    result.identifier,
                    authorization,
                    trust,
                    datetime.now(UTC),
                ),
            }
    print(
        FrozenJSON.freeze(
            {
                **extra,
                "pit_assessment": assessment,
                "manifest": result.to_dict(),
                "capture_pin": result.identifier,
                "plan_pin": plan.document.identifier,
            }
        ).payload_json
    )
    return 0 if result.to_dict()["ingestion"] == [["success", 1, None]] else 1
