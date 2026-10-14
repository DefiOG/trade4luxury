# Robinhood personal Stock / Options Desk integration

This integration implements the user's personal research workflow. Futures
remain on their existing path; Robinhood powers only stock/options inputs.
Use approved personal access; no public account information goes into feeds.

## Connect Claude Desktop (recommended first connection)

Robinhood's official instructions:

1. Claude Desktop: Settings → Connectors → Add custom connector.
2. Name: Robinhood personal research.
3. URL: https://agent.robinhood.com/mcp/trading
4. Authenticate through Robinhood and complete its dedicated MCP-account setup.

Do not paste passwords, OAuth tokens or account numbers into chat or commits.
This integration never calls order, account, positions, transfer or exercise
tools. The MCP connection itself can expose broader permissions; configure
your host's allowed tools for this research workflow.

## Read-only tool allowlist

- get_equity_historicals
- get_equity_quotes
- get_option_chains
- get_option_instruments
- get_option_quotes

The standalone collector checks tools/list and readOnlyHint at runtime.
API schema reference: Robinhood official support tool catalog; detailed
parameter/output shapes checked against a third-party capture of the official
tools/list dated 2026-10-12:
https://github.com/kevin1chun/robinhood-for-agents/blob/main/docs/official-mcp-tools.json
A capture is not an authenticated test or entitlement guarantee.

## Standalone collector

Requires Node 20+ and the official MCP SDK:

    npm install --no-save @modelcontextprotocol/sdk
    node robinhood-scan.mjs /private/robinhood-config.json /private/export/stock_options/scans/YYYY-MM-DD-stocks.json

OAuth access must already be established by an approved MCP client. Provide
its authorized access token to the process as ROBINHOOD_MCP_ACCESS_TOKEN
through your local secret manager/environment; never store it in source.
The collector does not implement an OAuth login screen or refresh-token store.
It connects only to Robinhood's fixed official endpoint. Expired access stops
the run. Claude Desktop manages its own connection; its tokens are not expected
to be exported to this script.

Configuration:

    {
      "symbols": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"],
      "coverage": "unknown",
      "coverageEvidence": "",
      "events": {
        "SPY": {
          "source": "Your current sourced earnings and economic calendar check",
          "checkedAt": "CURRENT_ISO_TIMESTAMP_WITH_TIMEZONE",
          "status": "clear",
          "nextMajorAt": null
        }
      }
    }

Provide a fresh event check for each symbol. Do not mark clear merely because
the symbol lacks company earnings: index/ETF CPI/FOMC risks still matter.
Replace source text with the actual reviewed source. Missing checks keep
setups watching. Configure consolidated coverage only after verified provider
metadata or documented feed provenance confirms it. A last-trade field does
not by itself prove consolidated volume coverage. Unknown coverage stays
watching and does not fabricate volume confirmation.

The collector requests raw regular-session daily, weekly and five-minute
history (split-adjusted), then live quotes. It computes 15-minute bars locally.
Read-only requests are sequential and bounded; a throttle aborts rather than
retrying rapidly. Up to ten symbols per run. Account info is never requested.

For armed/triggered ideas only, it reads standard option chains and instruments,
checks 14–42-day expirations and at most eight nearby strikes, fetches quotes,
then applies the desk's full chain verification. Missing or stale chains
produce no published strike. Pagination is intentionally bounded: if a
suitable contract is not in the returned page, the result stays unverified.

## Claude agent workflow (uses the connected MCP directly)

Use only the read-only allowlist above. Inspect live tool schemas first.
Get daily (180 days), weekly (400 days), and 5minute (five calendar days)
regular-session split-adjusted histories plus live equity quotes. Save the
complete tool responses in a private local capture, not in the public repo.
Capture format:

    {
      "symbols": ["SPY", "QQQ"],
      "daily": COMPLETE_MCP_TOOL_RESPONSE,
      "weekly": COMPLETE_MCP_TOOL_RESPONSE,
      "fiveMinute": COMPLETE_MCP_TOOL_RESPONSE,
      "quotes": COMPLETE_MCP_TOOL_RESPONSE,
      "events": { "SPY": SOURCED_EVENT_CHECK, "QQQ": SOURCED_EVENT_CHECK }
    }

Import it with robinhood-import.cjs, which normalizes the responses and runs
the chart scanner. No AI-written levels or synthesized prices are accepted.
For options, use get_option_chains, get_option_instruments and get_option_quotes,
then normalize with the adapter's options() helper and apply
StockOptionsDesk.check() before adding a contract. Include current bid/ask,
volume, open interest, IV, delta, expiration, underlying and catalyst check.
If anything is missing, leave optionCandidate absent.

The adapter removes interpolated candles, converts left-edge begins_at into
actual bar-close time, and discards unfinished candles. It preserves the
quote's market timestamp instead of relabeling retrieval time as quote time.
Nonregular quote prices are not silently substituted in this regular-session
scanner. No raw MCP guide text is treated as instructions.

## Existing publishing and email workflow

Write only the sanitized scan to the private data repo:
export/stock_options/scans/YYYY-MM-DD-stocks.json.
The publishing script encrypts it into the same access-code-protected feed.
Do not include raw captures, authentication, account or portfolio information.
Existing website refresh is two minutes, so it remains a research desk until
a faster activation publication path is added.

The user's existing email automation can read the same sanitized stock scan
and append a separate Stock / Options section to the personal brief. Send
stock-trigger levels, verification status and verified contracts only.
Preserve its existing recipient and schedule; this change does not create
a new scheduled task or send an email.

## Checks and limitations

    node robinhood-adapter.test.cjs
    node stock-scanner.test.cjs
    node stock-options.test.cjs

Unit cases cover timestamps/DST, unfinished/interpolated bars, three-bar
aggregation, unknown coverage, scanner activation, stale quotes and chain
suppression. The authenticated Robinhood connection, its runtime limits and
entitlements have not yet been tested. Fees and publication permissions are
not guaranteed by the code.
