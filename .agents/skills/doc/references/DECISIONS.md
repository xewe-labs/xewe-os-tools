# Decisions

Each line records one decision that a later change could undo by accident. Write it as a date,
the decision, and the reason, with the newest line last. The first two dates are the commits
that introduced the change (git log), so the decision may be a little older.

2026-10-13 Every release has a generated `release_card.md` in place of a hand-written test card, because the card records what ran on which tree and CI can check it against the tag.
2026-10-22 A release of the tools is one run of `scripts/release.sh`, and its notes, card and logs stay under the gitignored `build/release/<version>/`, with the card carried in the tag message.
2026-10-24 `doc/spec.md`, `doc/ci.md`, `doc/webui.md` and `tests/fixtures/releasecard/golden_card.md` keep their lowercase names, against the uppercase rule for Markdown, because `scripts/release.sh`, the `xewe` help text and the release card tests name those paths (see FIXED_PATHS.md).
2026-10-24 `releases/` keeps its lowercase `release_card.md` and `testcard.md` files, because CI and `releasecard.py` look for those names and the release notes of 3.0.0 and 3.0.1 cite them.
