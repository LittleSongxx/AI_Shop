# Customer-service v2 provenance audit

- Status: `HUMAN_VERIFIED_PROVENANCE_REVIEW_REQUIRED`
- Hash/label chain valid: `true`
- Label lifecycle: `HUMAN_VERIFIED_IMMUTABLE`
- Release gate eligible: `false`
- Final-unseen eligible: `false`

The 120 labels are structurally usable for development diagnostics. They are not yet release-grade evidence because reviewer independence/blinding provenance is incomplete.

## Findings

- **HIGH / REVIEW_A_FILLED_OPEN_SOURCE_MISSING**: The sealed review declares a filled open-sheet hash, but those bytes are unavailable.
- **HIGH / REVIEWA_EXPORT_HASH_SEMANTICS_INVALID**: openSheetSha256AtExport equals the filled input hash rather than the OPEN manifest's immutable export hash.
- **HIGH / REVIEWB_EXPORT_HASH_SEMANTICS_INVALID**: openSheetSha256AtExport equals the filled input hash rather than the OPEN manifest's immutable export hash.
- **HIGH / INDEPENDENCE_ATTESTATION_MISSING**: No signed reviewer-independence and label-blinding attestation is available.

## Required controls

- Obtain signed reviewer independence/blinding attestations, or replace the review with independently controlled review.
- Run the supplied 12-case blind independent re-audit; expand to all 60 additions if its preregistered gates fail.
- Keep this dataset out of final-unseen and release gates until both controls pass.

## Agreement

- Exact case agreement: `34/60` (`0.5666666666666667`)
- `handoffSeverity`: agreement `0.95`, Cohen's kappa `0.8886827458256029`
- `intent`: agreement `0.8666666666666667`, Cohen's kappa `0.8596491228070176`
- `riskLevel`: agreement `0.9666666666666667`, Cohen's kappa `0.9418040737148398`
- `shouldHandoff`: agreement `0.95`, Cohen's kappa `0.8793565683646112`
- `slots`: agreement `0.75`, Cohen's kappa `0.6927278934790031`
