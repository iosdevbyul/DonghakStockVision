"""Offline-only operations for original forward capture custody exports."""

import argparse
import os
from importlib.util import find_spec
from pathlib import Path

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.forward_capture import ForwardCapturePlan


def _bytes(path: Path | None) -> bytes | None:
    if path is None:
        return None
    require(
        not path.is_symlink() and path.is_file() and path.stat().st_size <= 65536,
        "bounded_evidence_file_required",
    )
    return path.read_bytes()


def run(args: argparse.Namespace, plan: ForwardCapturePlan) -> int:
    require(find_spec("pydantic") is not None, "custody_checks_require_existing_api_extra")
    # Lazy import preserves the base CLI's original optional dependency boundary.
    from donghak_stock_vision.storage.forward_custody import (
        ForwardOperationsConfig,
        ReturnedCustodyEvidence,
        assess_forward_operations,
        stage_forward_custody,
        verify_forward_custody,
    )

    require(not args.allow_network, "custody_commands_are_offline_only")
    require(
        args.pin is not None and args.plan_pin is not None,
        "independent_capture_and_plan_pins_required",
    )
    if args.action == "custody-stage":
        binding = stage_forward_custody(
            args.root, plan, args.plan_pin, args.attempt, args.pin, args.sequence
        )
        print(
            FrozenJSON.freeze(
                {
                    "binding": binding.to_dict(),
                    "binding_pin": binding.identifier,
                    "submission_performed": False,
                }
            ).payload_json
        )
        return 0
    if args.action == "custody-verify":
        data = _bytes(args.binding)
        require(
            data is not None and args.binding_pin is not None,
            "binding_and_independent_pin_required",
        )
        binding = FrozenJSON((data or b"").decode())
        verify_forward_custody(
            binding,
            args.binding_pin,
            args.root,
            plan,
            args.plan_pin,
            args.attempt,
            args.pin,
            args.sequence,
        )
        print(
            FrozenJSON.freeze(
                {
                    "binding_pin": binding.identifier,
                    "locally_verified": True,
                    "independent_custody_verified": False,
                }
            ).payload_json
        )
        return 0
    config = ForwardOperationsConfig()
    data = _bytes(args.operations_config)
    if data is not None:
        owner = FrozenJSON(data.decode())
        require(owner.identifier == args.operations_config_pin, "operations_config_pin_required")
        config = ForwardOperationsConfig.from_dict(owner.to_dict())
    returned = ReturnedCustodyEvidence(
        _bytes(args.acknowledgement),
        args.acknowledgement_pin,
        _bytes(args.returned_readback),
        args.returned_readback_pin,
    )
    report = assess_forward_operations(
        args.root,
        plan,
        args.plan_pin,
        args.attempt,
        args.pin,
        sequence=args.sequence,
        credential_present=bool(os.environ.get("KRX_API_KEY", "").strip()),
        config=config,
        returned=returned,
    )
    print(
        FrozenJSON.freeze(
            {"report": report.to_dict(), "report_pin": report.identifier}
        ).payload_json
    )
    # A successful inspection is not a provider/custody authorization.
    return 0
