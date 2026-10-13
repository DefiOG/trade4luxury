# Stock / Options Desk

A second desk in the existing encrypted website. The Futures Desk, its scans,
grader and history retain their existing behavior.

## Publishing path

The existing publish.py --data export command now also reads:

    export/stock_options/scans/<date>-stocks.json

in the private DefiOG/trade4luxury-data repository. These documents become
feed.optionsDesk.scans, encrypted with the existing feed. Do not place options
scans in export/scans: that collection belongs to the current futures grading
workflow. No provider credentials, API keys or account data go into exports.

A missing stock_options/scans directory produces an honest empty desk.
No examples or synthetic signals are added to the live feed.

## Scan format

A scan contains date (YYYY-MM-DD), createdAt (ISO timestamp with timezone),
headline, summary, and ideas. Each idea contains:

| Field | Meaning |
| --- | --- |
| id, ticker, setup | Stable setup ID, stock/ETF ticker, setup name |
| bias | bullish or bearish; bullish implies call-side |
| basis | daily, weekly or intraday; determines option timing |
| status | watching, armed, triggered, invalidated or expired |
| score | Numeric 0–10 setup quality; does not activate entry |
| thesis, currentZone, support | Plain language chart interpretation |
| trigger, invalidation, targets | Positive numbers in underlying stock dollars |
| triggerRule | Exact completed-candle reclaim, breakout or rejection rule |
| timeframeRead | Object with weekly, daily and intraday reads |
| quote | price, at (timezone required), source, feed (realtime/delayed/indicative) |
| riskNote | Setup-specific risk and catalyst considerations |
| activation | Required to record triggered state; see below |
| optionCandidate | Optional; hidden unless current chain passes all checks |

Targets must progress in the setup direction. The first target must offer at
least 2:1 from the stock trigger. Select the first real opposing structure;
do not move the target merely to manufacture 2:1.

Arming requires a real-time underlying quote no older than 90 seconds.
A triggered record requires activation.at, activation.barClosedAt,
activation.quoteAt, activation.entry, activation.feed = realtime, and
activation.evidence. The bar and quote must occur before activation and
within 90 seconds of activation. The actual entry must still offer 2:1.
Historical triggered records remain visible as recorded events; they do not
claim the old entry is executable now. Missing evidence downgrades display
to watching. The producer must expire or invalidate records as price changes.

## Options verification

optionCandidate fields:

    type, strike, expiration, underlyingPrice,
    bid, ask, volume, openInterest, iv, delta,
    source, verifiedAt, catalystCheck

iv is a decimal (0.25 = 25%); delta is signed. The display requires:

- Correct call/put side; strike within 5% of the supplied underlying price.
- Positive bid, ask >= bid, spread <= 10% of midpoint.
- Positive volume, open interest >= 100.
- Absolute delta 0.35–0.70; positive call delta, negative put delta.
- Positive IV, timestamp no older than 90 seconds, identifiable source.
- Explicit earnings/major-catalyst check.
- Daily setups: at least 14 days; weekly setups: at least 28 days.
- Valid setup evidence; invalidated/expired setups never display contracts.

These are conservative initial filters, not a tested trading edge.
An unverified or stale contract is suppressed, leaving expiration guidance.
Premium is quoted per share. Stock invalidation is not an option-price stop.
Spreads require a future multi-leg verification feature; no spread contracts
are inferred from a single-leg chain.

## Research-agent task instructions

Create a stock/options watchlist independently of the Futures Desk.
Use sourced charts and economic/earnings information; never remembered prices.

1. Weekly chart establishes structure; daily establishes setup; intraday
   establishes timing. Record all three reads. Missing data means watching.
2. Prioritize bullish call-side pullback/reclaim and breakout/retest patterns.
   Include bearish put-side patterns only when chart structure supports them.
3. Calculate trigger, structural invalidation, and targets from actual candles.
   If the first opposing structure gives less than 2:1, keep watching.
4. The scheduled brief publishes watches. A live-data monitor may arm/activate
   only after fresh quotes and completed-candle confirmation. A research agent
   must not mark triggered merely because the last price equals the trigger.
5. Quote delayed/indicative sources honestly; they cannot arm/activate entries.
6. Verify the current options chain before adding an optionCandidate. Otherwise
   omit it and publish timing only: daily 2–4 weeks; weekly 4–8 weeks;
   intraday weeklies only for aggressive momentum.
7. Export a plain JSON scan under export/stock_options/scans in the private
   data repo and run the existing encrypted publishing workflow.
8. Keep futures calls and futures trade grading unchanged.

## Data connection and results

The first version supplies the desk, feed routing and defensive display
validation. It does not yet supply a Schwab/Alpaca adapter, live provider adapter,
automated monitor or options-results grader. Connect a licensed source
server-side after credentials and entitlements are available. Existing feed
refresh is two minutes; live activation will require a faster publication path.

Stock-trigger outcomes and option returns need separate ledgers. Do not reuse
the futures grader to claim option P&L or a timestamped intraday fill.

## Verification

Run:

    node stock-options.test.cjs
    python3 -m py_compile publish.py

With a temporary empty export directory, publish.py should produce
optionsDesk.scans = [] and preserve all existing futures fields. Render the
encrypted site with its existing access flow and check both tabs on mobile.

## Technical scanner (implemented)

stock-scanner.cjs is provider-neutral. stock-scan.cjs consumes a normalized
snapshot and writes a private export compatible with this desk:

    node stock-scan.cjs --input /private/normalized-bars.json --output /private/export/stock_options/scans/YYYY-MM-DD-stocks.json
    node stock-scanner.test.cjs

Input is { symbols: [symbol snapshots], config?: {tickSize, volumeMultiple, minScore} }.
Each symbol includes ticker, daily, weekly, fifteenMinute, fiveMinute, quote,
and events. Candle objects are {open,high,low,close,volume,closedAt,complete,
session}; close timestamps require timezone offsets and must be unique and
ordered. session is regular or extended.

Minimum completed history: 51 daily, 21 weekly, 6 fifteen-minute and 21 five-minute
bars. For VWAP confirmation, the five-minute series must include the entire
current regular session starting with the 09:35 ET close, without gaps.
VWAP is a typical-price candle approximation, not tick-accurate traded VWAP.

quote includes source, at, price, feed = realtime, coverage = consolidated.
events includes source, checkedAt, status = clear or scheduled, nextMajorAt
(null when clear). Events are checked within the last 24 hours. A major event
inside 30 minutes blocks arming/activation; this is a basic event buffer,
not a complete earnings holding policy.

The scanner checks weekly 20-period and daily 20/50-period trend, ATR extension,
recent 15-minute reclaim/rejection levels, a completed 5-minute crossing,
15-minute candle alignment, full-session VWAP and relative volume. Targets
use confirmed daily/weekly pivots (two bars either side). It never extends
a target to manufacture 2:1. Delayed or partial-market feeds stay watching.
An actual quote that gaps beyond acceptable geometry cannot trigger.

The scanner quality score is technical only (maximum 8); it is not the
futures macro score. These are initial rules requiring historical validation,
not a demonstrated edge. Activation records use a quote reference, not a
verified brokerage fill. Contracts remain pending; no strike is invented.

The CLI processes a snapshot when invoked. Live scheduling, duplicate-event
handling, persisted setup lifecycle, broker adapter and website push delivery
still require integration. Provider secrets must remain server-side.
