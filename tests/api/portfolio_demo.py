"""Repository-only synthetic preparation using existing verified research fixtures.

No publication, server startup or live data. Requires the checkout and [api,learning,dev].
The controlled family guarantees mechanics, not predictions; linear/HGB use genuine
fixture inference and are allowed to produce no accepted orders or fills.
"""

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from donghak_stock_vision.api.historical_portfolios import LocalFiles, PublicationError, check
from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.portfolio import FiveTickerRequest
from donghak_stock_vision.backtest_cli import ReadOnlyMarketStore
from tests.api.helpers import snapshot_payload, write_snapshot
from tests.backtest.portfolio_helpers import TICKERS, execute_fixture, mechanics_run, prepared


def prepare(
    output: Path, family: str = "mechanics", *, scenario: str = "standard"
) -> dict[str, Any]:
    check(family in {"mechanics", "linear", "hgb"}, "unsupported_demo_family")
    check(
        scenario in {"standard", "stale_valuation"}
        and (scenario == "standard" or family == "mechanics"),
        "unsupported_demo_scenario",
    )
    root = output.absolute()
    check(not any(p in {".data", ".git", ".github"} for p in root.parts), "unsafe_demo_output")
    try:
        check(root.parent.resolve(strict=True) == root.parent, "invalid_demo_output")
        root.mkdir(mode=0o700)  # New directory only; never clobber or repair old artifacts.
    except FileExistsError as error:
        raise PublicationError("demo_output_exists") from error
    except OSError as error:
        raise PublicationError("invalid_demo_output") from error
    fixture = root / "sources"
    request, provider = prepared(
        fixture,
        "hgb" if family == "hgb" else "linear",
        controlled=family == "mechanics",
        omit_last=scenario == "stale_valuation",
    )
    d = request.document.to_dict()
    first = d["config"]["decision_dates"][0]
    market = ReadOnlyMarketStore(fixture / "market.db")
    try:
        dates = sorted(
            {
                b.trading_date.isoformat()
                for b in market.read(
                    TICKERS[0], date.fromisoformat(first), date.fromisoformat(d["end"])
                )
            }
        )
        check(len(dates) >= 2, "demo_fill_event_missing")
        # HGB derives features from the frozen requested range; retain the original
        # fixture history instead of claiming inference with a truncated warmup.
        if scenario == "standard":
            if family != "hgb":
                d["start"] = first
            d["end"] = dates[1]
        else:
            # Existing stale-mark mechanics fixture: keep its bounded original
            # range and fund the ticker whose final bar was deliberately omitted.
            d["config"]["valuation_policy"]["max_age_seconds"] = 1
            d["config"]["admission_policy"]["ordered_tickers"] = list(reversed(TICKERS))
        total_bars = sum(
            len(market.read(t, date.fromisoformat(d["start"]), date.fromisoformat(d["end"])))
            for t in TICKERS
        )
    finally:
        market.close()
    d["initial_cash"] = "1500"
    d["config"]["decision_dates"] = [first]
    bounded = FiveTickerRequest(FrozenJSON.freeze(d))
    source = (
        mechanics_run(fixture, bounded)
        if family == "mechanics"
        else execute_fixture(fixture, bounded, provider)
    )
    value = source.to_dict()
    check(value["detailed_report"]["scope"] == "synthetic_fixture", "synthetic_demo_required")
    if family == "mechanics":
        check(value["performance"]["fill_count"] >= 1, "demo_fill_missing")
        if scenario == "stale_valuation":
            check(
                value["detailed_report"]["final_valuation"]["complete"] is False,
                "demo_stale_valuation_missing",
            )
    else:
        check(
            any(
                row["signal_evidence_scope"] == "public_model_inference"
                for row in value["detailed_report"]["timeline"]
            ),
            "demo_inference_unavailable",
        )
    publication = root / "publication"
    publication.mkdir(mode=0o700)
    LocalFiles(publication).write("result.json", source.payload_json.encode())
    snapshot = snapshot_payload()
    snapshot["projectStatus"].update(
        productName="Synthetic portfolio demo",
        sourceLabel="synthetic fixture; not real-data research",
    )
    snapshot["coverage"].update(
        universe=5,
        totalBars=total_bars,
        startDate=d["start"],
        endDate=d["end"],
        currentIdentityVerified=0,
        historicalIdentityVerified=0,
        unresolvedSecurities=5,
        unexplainedTickerSessions=0,
    )
    snapshot["baselineBacktest"].update(
        title="Synthetic demo snapshot",
        summary="Baseline unavailable; use explicitly published synthetic portfolio resources.",
    )
    write_snapshot(root / "snapshot.json", snapshot)
    facts = {
        "scope": "synthetic_fixture_not_real_historical_research",
        "signalEvidenceScope": "controlled_mechanics_test_double"
        if family == "mechanics"
        else "public_model_inference",
        "family": family,
        "scenario": scenario,
        "tickers": list(TICKERS),
        "period": {"start": d["start"], "end": d["end"]},
        "resultHash": source.identifier,
        "reportHash": value["detailed_report_hash"],
        "fillCount": value["performance"]["fill_count"],
        "researchOnly": True,
        "published": False,
    }
    (root / "demo.json").write_text(json.dumps(facts, sort_keys=True, indent=2) + "\n")
    return facts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", required=True, type=Path, help="new canonical private directory"
    )
    parser.add_argument("--family", choices=("mechanics", "linear", "hgb"), default="mechanics")
    parser.add_argument("--scenario", choices=("standard", "stale_valuation"), default="standard")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(prepare(args.output, args.family, scenario=args.scenario), sort_keys=True))
    except PublicationError as error:
        raise SystemExit(error.code) from None


if __name__ == "__main__":
    main()
