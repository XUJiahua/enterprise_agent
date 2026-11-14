# Controlled POC results

| Path | Healthy acceptance | Faults blocked |
|---|---:|---:|
| baseline | 16/16 | 0/5 |
| zenstack | 16/16 | 5/5 |
| guarded | 16/16 | 5/5 |

Verifier adversarial probes: 7/7. Direct SQL probes: 6/6. Startup configuration probes: 2/2. Shared browser acceptance: 18/18. Synthetic evaluator controls: 4 committed-candidate evaluations plus 1 driver end-to-end control (not AI measurements).

These are hand-authored fault injections, not measured AI defect rates or first-pass productivity. A guarded field over-fetch fails closed with 403; ZenStack nullifies the denied field. Disabling the ZenStack policy client is an expected escaping boundary control.

Public CI model key configured for this run: unknown (local, CI Secret not checked). Model execution is not enabled by PR checks.
