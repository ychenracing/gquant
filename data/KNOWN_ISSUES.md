# Frozen snapshot: remaining limitations

The current stock volume column is shares. Observed Tencent units are **STAR: shares;
Shanghai mainboard and Shenzhen: lots x100**. The previous all-Shanghai-is-shares rule
understated eight mainboard volumes by 100x. See `VOLUME_UNIT_AUDIT.md` for the independent
Sina overlap, exact restoration method, coverage limits and preserved failure identities.
STAR correction is retained; it must never be divided by 100 again.

This is the original Tencent price snapshot with offline unit repairs. **Eastmoney was
not fully refreshed**; its requests failed. Prices remain unchanged. `amount=volume*close`
is a nominal proxy only; FusionEngine does not read it. Index volume=0 means unmodelled
liquidity. Independent Sina overlap covers 38 stocks, not every date; sz300776 lacks that
independent reference and is unchanged.

The default remains **max_adv_participation=0.08**. All fills of the same name share one
prior-known-volume budget per day; an explicit zero-volume observation is not replaced
with an older positive one. No-cap must be explicitly labelled diagnostic. Neither mode
certifies opening-auction, intraday-stop or large-account capacity. There is no orderbook,
minute-volume, impact, dividend-cash or full corporate-action execution model here.

`fetch_data.py` validates the entire fetched batch before writing CSVs/checksums/manifest;
a request or validation failure does not publish that batch. It is not a cross-process
transactional data service: do not refresh a directory concurrently with an active run;
validate and commit a complete snapshot before research. No live refresh was run here.
