---
name: github-issues
triggers: vilka öppna ärenden har jag i mitt github-repo, öppna issues, hur många issues finns det, pull requests i ett repo, kolla mitt repo på github, ärenden i cv_builder; list github issues or pull requests, open issues, how many issues, what issues do I have, PRs in a repo
---
The user wants issues or pull requests from a GitHub repository. Use the
`github_issues` (or `github_prs`) tool with `repo` as `"owner/name"`, e.g.
`github_issues(repo="sockulags/cv_builder")`. These work locally via the gh CLI
— do NOT offload to the coding agent (Codex/Claude) just to read GitHub. If you
know the owner from context or memory, use it; if the repo name is given without
an owner, ask once for the owner.
