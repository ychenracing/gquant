# Board-specific Tencent volume audit — 2026-09-07

## Finding and remedy

The assumption in 1769cde that **all Shanghai raw volume is already shares was wrong**.
The independent repository snapshot `trae/glmcsm/data/` was produced by
`trae/glmcsm/scripts/fetch.py` using **AkShare stock_zh_a_daily (Sina)**, whose official
schema documents `volume` in **shares**:
https://akshare.akfamily.xyz/data/stock/stock.html#id14

The cross-check uses actual overlapping daily volumes, not the self-generated
`amount=volume*close` identity. Across **38 stocks / 14125 symbol-days**:

- **8 Shanghai mainboard** stocks in 1769cde were about **1/100** of independent shares.
- **15 STAR** stocks already match independent shares **exactly**. They were NOT divided again.
- **15 Shenzhen** stocks match within **50 shares** (non-STAR Tencent lot rounding).

The mainboard remedy restores the eight original CSV blobs from **main@6ea40612** after
checking their SHA256 against that commit's `data/SHA256SUMS` and asserting exact equality
of all non-volume/non-amount columns. This is a data-correctness repair based on independent
observations, not a participation-limit change or parameter search. **8% stays 8%.**
The other corrected CSVs are unchanged from 1769cde; all **41** CSVs retain exactly
main@6ea40612's dates, OHLC and other non-volume/non-amount values. No prices, windows,
principals, benchmark metrics or pools were changed. `tests/test_volume_contract.py`
locks both the downloader conversion and the independent frozen overlap.

## Scope and limitations

This is an **offline repair of the original Tencent snapshot**, not an Eastmoney refresh.
The previous Eastmoney requests failed. Original `fetched_at` is retained. `amount` is still
a nominal adjusted-close-times-shares proxy, not actual turnover and not an independent
check. The 38-name reference overlap does not cover every date of the longer Tencent
snapshot; dates outside the ranges below inherit the observed board-unit convention.
**sz300776** has no independent Sina CSV here and is unchanged from the original snapshot.
The two index series remain volume=0 and are not traded. Future provider responses require
fresh unit validation; this evidence is not a universal or permanent provider guarantee.

Daily participation is a conservative modelling constraint, not evidence that a stated
quantity fills at the opening auction or the preset stop price. No intraday/auction/orderbook
refresh or market-impact calibration was performed.

## Reproducible overlap

| Symbol | Board | Common days | First / last | 1769cde / Sina median volume | Repaired maximum difference (shares) |
|---|---|---:|---|---:|---:|
| sh600183 | Shanghai mainboard | 359 | 2025-01-02 / 2026-06-30 | 0.01000000 | 50.000000 |
| sh600206 | Shanghai mainboard | 359 | 2025-01-02 / 2026-06-30 | 0.01000000 | 50.000000 |
| sh600487 | Shanghai mainboard | 377 | 2025-01-02 / 2026-07-24 | 0.01000000 | 50.000000 |
| sh601869 | Shanghai mainboard | 377 | 2025-01-02 / 2026-07-24 | 0.01000000 | 50.000000 |
| sh603019 | Shanghai mainboard | 349 | 2025-01-02 / 2026-06-30 | 0.01000000 | 50.000000 |
| sh603256 | Shanghai mainboard | 359 | 2025-01-02 / 2026-06-30 | 0.01000000 | 50.000000 |
| sh603986 | Shanghai mainboard | 377 | 2025-01-02 / 2026-07-24 | 0.01000000 | 50.000000 |
| sh605358 | Shanghai mainboard | 377 | 2025-01-02 / 2026-07-24 | 0.01000000 | 50.000000 |
| sh688008 | STAR | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688012 | STAR | 368 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688019 | STAR | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688037 | STAR | 374 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688041 | STAR | 367 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688072 | STAR | 367 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688082 | STAR | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688120 | STAR | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688256 | STAR | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688268 | STAR | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688300 | STAR | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688347 | STAR | 349 | 2025-01-02 / 2026-06-30 | 1.00000000 | 0.000000 |
| sh688361 | STAR | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688498 | STAR | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 0.000000 |
| sh688519 | STAR | 359 | 2025-01-02 / 2026-06-30 | 1.00000000 | 0.000000 |
| sz000977 | Shenzhen | 359 | 2025-01-02 / 2026-06-30 | 0.99999996 | 50.000000 |
| sz000988 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 50.000000 |
| sz002281 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 50.000000 |
| sz002371 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 1.00000035 | 50.000000 |
| sz002409 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 50.000000 |
| sz002463 | Shenzhen | 359 | 2025-01-02 / 2026-06-30 | 1.00000005 | 50.000000 |
| sz300054 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 50.000000 |
| sz300223 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 0.99999996 | 50.000000 |
| sz300236 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 50.000000 |
| sz300308 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 0.99999996 | 49.000000 |
| sz300394 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 1.00000011 | 50.000000 |
| sz300502 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 1.00000000 | 50.000000 |
| sz300604 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 1.00000011 | 50.000000 |
| sz300655 | Shenzhen | 377 | 2025-01-02 / 2026-07-24 | 1.00000001 | 50.000000 |
| sz300666 | Shenzhen | 372 | 2025-01-02 / 2026-07-24 | 1.00000000 | 50.000000 |

## Evidence identity

The reference snapshot itself is untouched. Its producer path and all comparison inputs
were recovered from source archive **9c00ff47e8958268fb54d465ae95104c6d14bef3**, engineering
artifact **10021900611** from run **34130569737**. That artifact was SHA256-verified as
`d50948b8a08694ce2557ead0cae2ba793e5ba49a4296c2d87b782405064a7016` before extraction.
The original failed 8% candidates remain failed historical evidence, separately indexed
in `../evidence/README.md`. The restored data set has its own new `SHA256SUMS`.
