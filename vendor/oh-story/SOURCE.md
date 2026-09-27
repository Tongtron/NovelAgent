# Vendored oh-story Skills

- Repository: https://github.com/worldwonderer/oh-story-claudecode
- Pinned commit: `964d6bfdb7b78b225591e4b35bfa00d245d4f9a2`
- License: MIT; see `LICENSE` in this directory.
- Included Skill paths:
  - `skills/story-long-write`
  - `skills/story-deslop`

NovelAgent uses these files as versioned writing instructions and reference
data. The host selects one genre prose card, builds structured chapter-plan
context, sends approved excerpts to the configured LLM, and performs a local
post-polish scan. It does not run upstream setup commands, hooks, custom
agents, or file-oriented project management. SQLite remains NovelAgent's sole
source of truth. The upstream Node scripts are retained for provenance but
are not executed by the Python service or its container.
