# Evidence identities and acceptance boundaries

## Current acceptance

`fusion/config.py` defaults to **0.08**, never no-cap. `glmqwen-verify` records the exact
checked-out HEAD, Python/dependency versions, source archive, config, frozen data hashes,
and target hash. Its engineering and economics jobs are separate. The economic job
requires the four frozen-reference gates at 8% and the actual-fill/account audit.
Capital scans and increased-cost failures are explicitly diagnostic, not silently waived
formal requirements. PR checks and downloaded artifacts are the status authority; a source
commit or a generator's green status is not a final-HEAD check.

Current outputs are artifact files `formal-8pct-scorecard.json` (including a fixed capital
scan), `formal-8pct-correctness.json` (continuous accounts, all four ledgers and costs), and
`diagnostic-no-cap-scorecard.json`. Outputs do not overwrite the historical files below.

## Historical failures: retain, never relabel as passed

- Original failure: run **34124222214**, job **101748980438**, source
  **3a7a264212dbd1d0e2a2978cb51bee962ded9fa5**; 72 tests passed but **8% 0/4**.
  See `historical-8pct-failure.md`, copied unchanged from the handoff.
- Reproduced on **9c00ff47e8958268fb54d465ae95104c6d14bef3**, run **34130569737**:
  engineering job **101769422060** passed, economic job **101769422567** failed.
  `historical-9c00ff47-8pct.json` preserves the result bytes. This source is 1769cde plus
  read-only CI only, before the newly found Shanghai-mainboard unit repair.
- The later unit finding explains a defect in those candidates; it does not retroactively
  turn their failed evidence into accepted evidence. See `../data/VOLUME_UNIT_AUDIT.md`.

## Historical no-cap artifacts, not current defaults

The three existing root JSON files below belong to **1769cde150831f9602faf13f666add7c9fd9962c**
and **max_adv_participation=0.0**. Their contents are preserved, not promoted to 8% evidence.
The generator run **34125227751** started on **a1b1400a69e9d4734a9ac6b94e215b593ddb53d3**;
1769cde originally had zero check-runs. No specific user approval for disabling the cap was
recovered. Current work restores 8%, so no new no-cap approval is assumed or needed.

| Historical file | SHA256 |
|---|---|
| `../final-scorecard.json` | `6ddfc3f739874deec560317e6f7961b16f9bf7732b73cfe4b10bf2892d552db0` |
| `../capital-sweep-scorecard.json` | `a705cc2f6cc2c6c1968d1e0944a86aa91c6935597343bb4898e5284f1cd3386b` |
| `../economic-correctness.json` | `5f84fc5dbd60cb656dd6806d92144e7af67a97cf5449c3be8f68af14546d1e84` |

Old no-cap scorecard: **4/4**; capital scan: **23/24**, with glmcsm at 1m failing.
Old no-cap diagnostic window: return **-2.744774859339183%**, MDD **-21.87280481381123%**.
Old no-cap continuous 2026-06-20..08-30: return **-2.757322781761684%**, MDD
**-9.508492076600927%**, anchor June 18, actual June 22..August 28. These retain their own
identity even where corrected 8% outputs happen to have the same numbers.

All target windows have been observed and used for research. The historical diagnostic
window overlaps research/target evidence and is not clean OOS. Four frozen-reference rows
are not four fresh independent reruns: track/momentum share glmqwen's same window/capital,
and momentum has no underlying data here. No-cap capital scans never certify capacity.
