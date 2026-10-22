# releases

One folder per xewe-os-tools release, named after the version. The release card is
[doc/spec.md](../doc/spec.md) §14; how a release is made is
[scripts/README.md](../scripts/README.md).

| File | Versions | What it is |
|---|---|---|
| `notes.txt` | every version | the release notes, written by hand; the GitHub Release text, embedded verbatim in the card |
| `release_card.md` | 3.1.0 onward | the generated release card: what was tested, on which tree, with which result |
| `testcard.md` | 3.0.0 and 3.0.1 | the hand-written test card of those releases |
| `logs/` | prepare runs | the card run's logs; gitignored, kept only on the machine that ran it |

`scripts/release.sh` writes nothing here: its notes, card and logs are under `build/release/<version>/`
(gitignored), and the card travels in the tag message. The `card` job of `.github/workflows/ci.yml`
still finds a committed `releases/*/release_card.md` of the tree it tests, so these files stay tracked in git.
