# Third-party dataset notice

`telco-lab` does not redistribute third-party dataset files. It contains only
an audited catalog that lets a user explicitly download exact upstream bytes
into a local, ignored workspace after accepting the recorded license.

## BubbleRAN Open Telco Datasets

- Project: <https://github.com/bubbleran/open-telco-datasets>
- Attribution: BubbleRAN Open Telco Datasets contributors
- Pinned revision: `fa4e3333855d64474e710bc5bebf11a9ec075e0b`
- Dataset: `datasets/anomaly-detection-persistent-interference`
- License: Creative Commons Attribution-ShareAlike 4.0 International
  (`CC-BY-SA-4.0`)
- License terms: <https://creativecommons.org/licenses/by-sa/4.0/>
- Pinned repository license evidence:
  <https://raw.githubusercontent.com/bubbleran/open-telco-datasets/fa4e3333855d64474e710bc5bebf11a9ec075e0b/LICENSE>
- License evidence SHA-256:
  `a25b2415e77fbec63d46ddf10c638218cffdcf63875386c59e766f4fba59897a`
- Pinned dataset README SHA-256:
  `157ec6a274b079fce2faaa18560025f6819c9c295f79212ef8d456348851ea30`
- Evidence reviewed: 2026-08-30

The pinned repository license states that datasets and documentation are
licensed under CC BY-SA 4.0 unless a dataset README says otherwise. The pinned
persistent-interference README contains no contrary license statement. Exact
artifact sizes and SHA-256 digests are stored in
`src/telco_lab/catalogs/default.json` and copied into the local workspace lock.

Redistribution, creation of adapted datasets, or use outside an internal test
environment remains subject to attribution and ShareAlike obligations and to
the company's own legal/compliance review.

## RCAEval

- Project: <https://huggingface.co/datasets/phamquiluan/RCAEval>
- Attribution: RCAEval dataset contributors
- Pinned revision: `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`
- Selected slice: `cases.parquet` plus metrics, logs, and traces for repetition 1
  of the RE2 Online Boutique CPU cases for `checkoutservice`,
  `currencyservice`, `emailservice`, `productcatalogservice`, and
  `recommendationservice`
- Catalog closure: 16 opaque Parquet resources, 53,433,532 bytes total
- License: MIT
- License terms: <https://opensource.org/license/mit/>
- Pinned dataset-card license evidence:
  <https://huggingface.co/datasets/phamquiluan/RCAEval/resolve/afeacb11bcc94dadfd1c8f483ee4377b2b8b614e/README.md>
- License evidence SHA-256:
  `c2990bbe2e040a8d2f55fdd47c4f47f02223d8ea098e5d6e8851585a64956a0f`
- Evidence reviewed: 2026-08-31

The catalog treats every selected file as opaque input: it records only a
frozen upstream URL, exact byte count, SHA-256 digest, media type, and license
evidence. Catalog inclusion does not claim a validated RCAEval schema,
canonical projection, benchmark result, privacy classification, or production
fitness. Consumers must use the held-handle verification boundary before any
future parser interprets these bytes. The files remain upstream and are fetched
only after explicit MIT acceptance into the ignored local workspace.
