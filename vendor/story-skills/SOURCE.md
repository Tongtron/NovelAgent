# Vendored Story Skills

- Upstream: https://github.com/danjdewhurst/story-skills
- Commit: `c482d48f4eb9b488f033a77a51f9fae55cc0d75f`
- License: MIT, see `LICENSE`
- Included skills: `revision-continuity`, `story-maintenance`

NovelAgent keeps SQLite as its only authoritative story store. The runtime
loads the approved `revision-continuity` instructions and implements the
relevant deterministic continuity contracts in Python. The bundled
`story-maintenance/scripts/story.js` file is retained with the upstream skill
for provenance and manual compatibility; the application does not execute it
against NovelAgent data.
