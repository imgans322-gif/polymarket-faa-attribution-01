[![make.fun · polymarket-faa-attribution-01](https://make.fun/badge/polymarket-faa-attribution-01.svg)](https://make.fun/bounty/polymarket-faa-attribution-01?utm_source=github&utm_medium=readme&utm_campaign=polymarket-faa-attribution-01)
[![rank](https://make.fun/badge/polymarket-faa-attribution-01/imgans322-gif/polymarket-faa-attribution-01/rank.svg)](https://make.fun/bounty/polymarket-faa-attribution-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=polymarket-faa-attribution-01)
[![reward](https://make.fun/badge/polymarket-faa-attribution-01/imgans322-gif/polymarket-faa-attribution-01/reward.svg)](https://make.fun/bounty/polymarket-faa-attribution-01?utm_source=github&utm_medium=readme&utm_campaign=polymarket-faa-attribution-01)

# FAA attribution forecaster — polymarket-faa-attribution-01

A deterministic, standard-library-only program (`main.py`, Python 3.13) that
forecasts Polymarket Yes prices four hours ahead, minute by minute, as five
quantiles (0.05, 0.25, 0.5, 0.75, 0.95) per market per minute.

## How it works

Prediction-market prices on a 1-minute grid are a step process: most minutes
nothing moves, then there is a tick, and now and then a jump — and how jumpy
a market is changes over the day. One fixed spread for every market (the
house baseline's 0.0025 per square-root minute) is therefore wrong in both
directions at once. For each market in the case, this program instead
measures the market's own recent step size on two timescales and takes the
larger:

- recent: EWMA of the absolute 1-minute price change, half-life 60 minutes,
  over the trailing 720 minutes;
- slow: mean absolute 1-minute change over the trailing 960 minutes;

each change winsorised to ±0.10 first, the combined estimate clamped to
[0.0002, 0.006] per square-root minute. Then:

    q_tau(h) = clamp(x + m_tau * sigma * sqrt(h), 0, 1), rounded to 6 dp,

where x is the price at the checkpoint, h = 1..240 is the minute being
forecast and the multipliers m_tau are (-1.645, -0.40, 0, 0.40, 1.645).
The outer pair keeps the normal 5%/95% spacing; the inner pair is pulled in
from the normal's 0.674 because the interquartile range of a step process
grows much more slowly than its tails. The median stays at the last price:
every drift term tried made the practice loss worse.

Taking the max of the two timescales is the point: a market that just turned
volatile gets wide quantiles within the hour, while a market that has been
quiet for a day stays narrow even if it twitched once this morning. That
matters for the final, whose four FAA markets are young and jumpy — the
rules record 20-point minute moves in their first hours.

## Local practice score

Scored locally by chaining the practice cases — the 240 minutes after a
checkpoint are exactly the tail of the next case's price series (the last
case's future was fetched from Polymarket's public price history; the
scorer reproduces the published baseline exactly, loss 0.363589, median
error 0.767503, coverage 0.949642, which validates it). On all six cases
this program loses **0.268600** against the house baseline's **0.363589**
(median error 0.767503, 90% coverage 0.901); on cases p00–p04 alone it is
0.306492 against the baseline's 0.392518.

About a hundred configurations were tried on the practice cases. The
rejected ones: any drift term, an additive linear jump term, the standard
deviation of changes instead of the mean absolute change, partial pooling
of sigmas toward the case median, slow windows of 1440/2880 minutes, and
horizon exponents other than 0.5. The plain slow-only mean over 960 minutes
scores 0.269332, statistically tied with this entry; the max of the two
timescales is kept because it reacts within the hour when a market wakes
up, wins the wildest practice case, and its coverage lands on the nominal
0.90.

## Run locally

The program reads every `*.in` file in `$MAKEFUN_INPUT_DIR` (else `/input`),
in name order, and writes the matching `<id>.out` to `$MAKEFUN_OUTPUT_DIR`
(else `/output`). No network, no randomness, no third-party packages: two
runs on the same case are byte-identical, and a case takes about a tenth of
a second (limit 8 s).

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=/path/to/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

## A note on the kit

The -01 kit itself could not be downloaded at build time: the make.fun API
answered `no competition polymarket-faa-attribution-01` for the kit, the
requirements endpoint, and the live competitions list, even though the
competition was listed (with this slug, deadline 2026-11-15 09:00 UTC) in
data fetched earlier. The build therefore used the sibling competition's
kit (polymarket-faa-attribution-02, same task family, same program/v1
harness, same I/O format, same practice cases cut from the NVIDIA event,
same published baseline loss 0.363589) as a stand-in. The entry program
itself is format-identical for both kits; if the -01 kit appears with
different practice data, re-grade against it before submitting.
