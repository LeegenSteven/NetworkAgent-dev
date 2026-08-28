# LTE demo data notice

The source performance and cell-trace samples were provided by DigitalRoute as
synthetic, realistic telecom records for the Apache-2.0 licensed
`telco-autonomous-networks-data-demo` project. They do not come from a live
telecom network.

This unified repository retains the complete `performance.csv` sample. The
original cell-trace file is not copied: `safe-cell-traces.csv` is a mechanical
projection containing only procedure type, timestamps, eNodeB/Cell identifiers,
and the aggregated S1 connection outcome needed by the deterministic RCA. IMSI,
MSISDN, IMEI/IMEISV and other subscriber/device identifiers are deliberately
excluded.

The source timestamps have no timezone metadata. The Local Profile explicitly
interprets them as UTC for deterministic tests; this is a documented assumption,
not a claim about the data producer's original timezone.
