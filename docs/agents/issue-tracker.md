# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues in `nishuzumi/leelen_home3`. Use the `gh` CLI for all operations.

## Conventions

- Create issues with `gh issue create`.
- Read issues with `gh issue view <number> --comments`.
- List issues with `gh issue list` and JSON output when structured data is needed.
- Comment, label, and close issues with the corresponding `gh issue` commands.
- Infer the repository from the local Git remotes.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub shares one number space across issues and pull requests. Resolve an ambiguous number with `gh pr view <number>` and fall back to `gh issue view <number>`.

## Skill operations

- When a skill says "publish to the issue tracker", create a GitHub issue.
- When a skill says "fetch the relevant ticket", run `gh issue view <number> --comments`.
- For wayfinding, use a labelled map issue with linked child issues and native issue dependencies where available.
