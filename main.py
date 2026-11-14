# polymarket-faa-attribution-01 entry (Python 3.13, standard library only).
#
# Idea, in one paragraph: a prediction market's Yes price on a 1-minute grid
# is a step process -- silent for minutes, then a tick, now and then a jump
# -- and how jumpy a market is changes over the day. A single fixed spread
# for every market (the house baseline's 0.0025 per square-root minute) is
# therefore wrong twice over: too wide for the many markets that have barely
# moved, too narrow for one that just woke up, and blind to whether the
# waking happened an hour ago or yesterday. So this program measures each
# market's own step size from the case it is given, on two timescales at
# once, and builds the five quantiles around the market's last price from
# that measurement:
#
#   recent = EWMA of |1-minute change| (half-life 60 min, trailing 720 min)
#   slow   = mean |1-minute change| over the trailing 960 minutes
#   sigma  = max(recent, slow), each change winsorised to +/-0.10 first so
#            one wild print cannot dominate, then scaled and clamped to
#            [0.0002, 0.006] per square-root minute;
#   q_tau(h) = clamp(x + drift*h + m_tau * sigma * h**0.5, 0, 1),
#            rounded to 6 decimals,
#
# where x is the price at the checkpoint, h = 1..horizon is the minute being
# forecast, m_tau = (-1.645, -0.40, 0, 0.40, 1.645), and drift is a heavily
# shrunk recent trend (off by default: on the practice cases every drift
# tried made the loss worse, so the median stays at the checkpoint price
# unless tuning says otherwise). Taking the max of the two timescales, rather
# than their average, is the point: a market that just turned volatile gets
# wide quantiles at once, while a market that has been quiet for a day stays
# narrow even if it twitched once this morning. The outer multipliers keep
# the normal 5%/95% spacing; the inner pair is pulled in from the normal's
# 0.674 because the interquartile range of a step process grows much more
# slowly than its tails -- most of the time the price simply has not moved
# yet.
#
# Calibration note: the constants were chosen on the kit's six practice
# cases only, scored locally by chaining the cases (the minutes after a
# checkpoint are exactly the tail of the next case's price series; the last
# case's future was fetched from Polymarket's public price history and
# reproduces the published baseline exactly -- see the work notes). On
# those six cases this program loses 0.268600 against the house baseline's
# 0.363589 (median error 0.767503, the same as the baseline's -- both centre
# the median on the checkpoint price, so the whole gain is in the spread;
# 90% coverage 0.901 -- essentially the nominal 0.90). About a hundred configurations were tried; the ones that
# lost are worth naming so nobody re-tries them: a drift term at any
# shrinkage (momentum is noise here), an additive linear jump term, the
# standard deviation of changes instead of the mean absolute change,
# partial pooling of sigmas toward the case median, longer slow windows
# (1440/2880 min), and horizon exponents other than 0.5. The plain
# slow-only mean over 960 minutes scores 0.269332, statistically tied with
# this entry; the max of the two timescales is kept because it reacts
# within the hour when a market wakes up (the final's four FAA markets are
# young and jumpy -- the rules record 20-point minute moves in their first
# hours), it wins the wildest practice case, and its coverage lands on the
# nominal 0.90. The scorer and the tuning grid live outside this
# repository.
#
# The program is fully deterministic (no randomness, no clock, no network):
# two runs on the same case write byte-identical output. It reads every
# *.in file in $MAKEFUN_INPUT_DIR (else /input), in name order, and writes
# the matching <id>.out to $MAKEFUN_OUTPUT_DIR (else /output).

import json
import math
import os
import sys

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

# The quantile levels the contest scores, in row order.
QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]
# Quantile multipliers per level: (5%, 25%, 50%, 75%, 95%).
MULT_OUTER = 1.645
MULT_INNER = 0.40
# Trailing windows, in minutes, for the two volatility timescales.
RECENT_WINDOW_MINUTES = 720
SLOW_WINDOW_MINUTES = 960
# Half-life, in minutes, of the recent timescale's EWMA.
RECENT_HALF_LIFE_MINUTES = 60.0
# How the two timescales combine: "max" takes the larger (react fast to a
# market waking up, stay narrow while it sleeps); "blend" averages them
# with BLEND_RECENT_WEIGHT on the recent one.
COMBINE_MODE = "max"
BLEND_RECENT_WEIGHT = 0.5
# A single 1-minute change is winsorised to this magnitude before averaging.
WINSOR_LIMIT = 0.10
# Overall scale applied to the combined sigma estimate.
SIGMA_SCALE = 1.0
# How the per-minute changes are turned into a sigma: "mean" takes the mean
# absolute change (robust for a step process, where most minutes do not
# move at all); "std" takes the standard deviation of the changes instead.
SIGMA_MODE = "mean"
# Additive linear-in-horizon term in the spread, in price units per minute:
# spread(h) = sigma * h**HORIZON_EXPONENT + JUMP_PER_MINUTE * h. A small
# positive value prices the occasional jump that a pure random walk's
# square-root growth understates at long horizons. 0.0 disables it.
JUMP_PER_MINUTE = 0.0
# Partial pooling: each market's sigma is pulled this far toward the
# case's median sigma (0.0 = no pooling, 1.0 = every market gets the
# median). Young markets with little history borrow strength from the
# case; established ones keep their own measurement.
SHRINK_TO_MEDIAN = 0.0
# Bounds for the per-market sigma estimate, in price units per sqrt(minute).
SIGMA_FLOOR = 0.0002
SIGMA_CAP = 0.006
# Fallback sigma when a market has no measurable change at all (a history
# shorter than two prices): the house baseline's fixed spread.
SIGMA_FALLBACK = 0.0025
# Horizon growth: spread grows like h ** HORIZON_EXPONENT (0.5 = random walk).
HORIZON_EXPONENT = 0.5
# Drift: mean 1-minute change over the trailing DRIFT_WINDOW_MINUTES,
# shrunk by DRIFT_SHRINK, added per minute ahead. 0.0 = median stays at
# the checkpoint price.
DRIFT_WINDOW_MINUTES = 240
DRIFT_SHRINK = 0.0


def price6(value):
    """Clamp a price to [0, 1] and round it to 6 decimals (never -0.0)."""
    clamped = min(1.0, max(0.0, value))
    return round(clamped, 6) + 0.0


def _changes(prices, window_minutes):
    """Winsorised signed 1-minute changes over the trailing window."""
    if len(prices) < 2:
        return []
    start = max(1, len(prices) - window_minutes)
    out = []
    for i in range(start, len(prices)):
        change = prices[i] - prices[i - 1]
        if change > WINSOR_LIMIT:
            change = WINSOR_LIMIT
        elif change < -WINSOR_LIMIT:
            change = -WINSOR_LIMIT
        out.append(change)
    return out


def _ewma(values, half_life):
    """Exponentially weighted mean, most recent value weighted most."""
    if not values:
        return None
    alpha = 1.0 - 0.5 ** (1.0 / half_life)
    acc = values[0]
    for value in values[1:]:
        acc = alpha * value + (1.0 - alpha) * acc
    return acc


def _summarise(changes):
    """One number for a list of winsorised signed 1-minute changes."""
    if not changes:
        return None
    if SIGMA_MODE == "std":
        mean = sum(changes) / len(changes)
        var = sum((c - mean) ** 2 for c in changes) / len(changes)
        return math.sqrt(var)
    return sum(abs(c) for c in changes) / len(changes)


def estimate_sigma(prices):
    """Per-market step size in price units per square-root minute."""
    if len(prices) < 2:
        return SIGMA_FALLBACK
    recent_changes = [abs(c) for c in _changes(prices, RECENT_WINDOW_MINUTES)]
    recent = _ewma(recent_changes, RECENT_HALF_LIFE_MINUTES)
    slow_changes = _changes(prices, SLOW_WINDOW_MINUTES)
    slow = _summarise(slow_changes)
    if recent is None and slow is None:
        return SIGMA_FALLBACK
    if recent is None:
        combined = slow
    elif slow is None:
        combined = recent
    elif COMBINE_MODE == "max":
        combined = max(recent, slow)
    else:
        combined = BLEND_RECENT_WEIGHT * recent + (1.0 - BLEND_RECENT_WEIGHT) * slow
    sigma = SIGMA_SCALE * combined
    return min(SIGMA_CAP, max(SIGMA_FLOOR, sigma))


def drift_per_minute(prices):
    """Heavily shrunk recent trend, in price units per minute (0 if off)."""
    if DRIFT_SHRINK == 0.0 or len(prices) < 2:
        return 0.0
    changes = _changes(prices, DRIFT_WINDOW_MINUTES)
    if not changes:
        return 0.0
    return DRIFT_SHRINK * sum(changes) / len(changes)


def forecast_market(prices, horizon, sigma=None):
    """The horizon x 5 quantile rows for one market."""
    if prices:
        centre = prices[-1]
        if sigma is None:
            sigma = estimate_sigma(prices)
        drift = drift_per_minute(prices)
    else:
        # No history at all: centre on 0.5 with the fallback spread rather
        # than crashing, which would invalidate the whole case.
        centre = 0.5
        sigma = SIGMA_FALLBACK
        drift = 0.0
    multipliers = [-MULT_OUTER, -MULT_INNER, 0.0, MULT_INNER, MULT_OUTER]
    rows = []
    for h in range(1, horizon + 1):
        spread = sigma * (h ** HORIZON_EXPONENT) + JUMP_PER_MINUTE * h
        mid = centre + drift * h
        rows.append([price6(mid + m * spread) for m in multipliers])
    return rows


def run(input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    names = sorted(name for name in os.listdir(input_dir) if name.endswith(".in"))
    for name in names:
        case_id = name[: -len(".in")]
        with open(os.path.join(input_dir, name), "r", encoding="utf-8") as handle:
            case = json.load(handle)
        if list(case["quantiles"]) != QUANTILES:
            raise ValueError(
                f"{name} asks for quantiles {case['quantiles']}, not {QUANTILES}"
            )
        horizon = int(case["horizon_minutes"])
        markets = case["markets"]
        sigmas = [estimate_sigma(m["prices"]) for m in markets]
        if SHRINK_TO_MEDIAN > 0.0 and sigmas:
            ordered = sorted(sigmas)
            n = len(ordered)
            median = (
                ordered[n // 2]
                if n % 2 == 1
                else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0
            )
            sigmas = [
                (1.0 - SHRINK_TO_MEDIAN) * s + SHRINK_TO_MEDIAN * median
                for s in sigmas
            ]
        forecasts = [
            {"id": market["id"], "q": forecast_market(market["prices"], horizon, sigma)}
            for market, sigma in zip(markets, sigmas)
        ]
        payload = json.dumps({"forecasts": forecasts}, separators=(",", ":"))
        out_path = os.path.join(output_dir, case_id + ".out")
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")


def main():
    run(INPUT_DIR, OUTPUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
