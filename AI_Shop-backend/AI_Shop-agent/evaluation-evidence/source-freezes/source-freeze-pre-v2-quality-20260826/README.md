# Dirty-worktree source freeze: pre-v2 quality

This immutable local baseline binds the exact repository state used before v2 evidence canonicalization and v2-driven fixes.

- It is a source/provenance checkpoint, not model-quality evidence and not a release gate.
- `tracked.patch` reconstructs tracked changes when applied to the recorded base commit.
- `untracked-files.json` binds every untracked regular file by path, size and SHA-256; their bytes remain in the working tree and should enter a reviewed checkpoint commit later.
- The evaluation and Agent runtime fingerprints bind the code/configuration scopes actually consumed by the evaluators and runtime.
- A final post-fix source freeze must be generated before any new result is promoted.
