# Source and privacy boundary

The benchmark's canonical owner is `measured-intuition/prospecting/benchmarks/` in the private 10x repository. Its only production-code dependencies are `_system/crm/scan_efficiency.py` and `measured-intuition/prospecting/scan-reddit-reader.js`, with the latter's existing `tests/test-scan-reddit-reader.mjs`. No evaluation result changes either file.

For public export, copy those three source/test files unchanged into a clean repository beside the benchmark runner and controlled cases. Keep SHA-256 provenance for each. The public-source cases are short paraphrases of the linked original postings; synthetic cases have no actual buyer or contact. No live source capture, private conversation, source registry, CRM row, email, phone, application, saved cutoffs, credentials or browser cookies may enter the export.

The actual pipeline includes source readers, durable captured observations, model task envelopes, interpretation, deduplication, ranking, CRM and human review. This public slice only exercises offline planning plus one generic public-source reader. Source/claim correctness beyond the reader fixtures and all model-quality outcomes remain unmeasured.

No new license is granted or implied by this export. Confirm the repository state, paths and secret scan before any public push.
