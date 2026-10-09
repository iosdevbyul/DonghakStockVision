# Corporate-action document review index

The DART acquisition stage now preserves all planned official source bytes. This
review stage builds a deterministic local index from those bytes without approving
an economic event or an effective date.

The index extracts review aids only:

- archive entry names and SHA-256 hashes
- normalized text length
- known corporate-action term hits
- short surrounding snippets for matched terms
- normalized date-string candidates

A detected term is not an event. A detected date is not an effective date. The
output keeps both event and effective-date approval flags false and never clears
the corporate-action gate.

After all acquisition documents are present, run:

```bash
rm -f /tmp/corporate-action-document-review.json

.venv/bin/python -m donghak_stock_vision.backtest.review_corporate_action_documents \
  --root . \
  --sources .data/corporate-action-documents \
  --output /tmp/corporate-action-document-review.json
```

The command prints aggregate counts and a deterministic artifact fingerprint.
The full local JSON remains a review aid for the next evidence stage.

This index does not establish authoritative no-event semantics or whole-period
historical security identity. Those remain independent requirements of the final
coverage contract.
