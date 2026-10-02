# Work panel

Run `work` for an on-demand fzf panel containing your authored GitHub PRs,
requested reviews, and assigned unresolved Jira tickets. It performs no network
requests during shell startup. `keys work` lists the same shortcuts used by the
panel: Enter or `o` opens an item, `r` starts a PR review, `n` creates a Jira
ticket, Ctrl-R refreshes, `?` shows help, and `q` leaves.

`inbox` remains the implicit default. The supplied `reviews`, `mine`, and `issues`
profiles select requested reviews, your authored PRs, and assigned GitHub issues.
Type `work ` and press Tab to page through the available profile names. Completion
reads local configuration only; it does not authenticate or make API requests.

```sh
work reviews
work list issues --json
work profiles add triage 'repo:org/repo is:open is:pr label:"needs review"'
work profiles add bugs 'repo:org/repo is:open is:issue label:bug' --account enterprise
work triage
work profiles default triage
work profiles default inbox
```

Named profiles use ordinary GitHub search queries. Omit `--account` to search all
configured/discovered accounts; repeat it to select several named accounts.
`@me` refers to the verified account for each request. `work --profile NAME` and
`work list NAME` explicitly override the configured default. Jira's assigned
queue remains present alongside every GitHub profile. GitHub issues open in the
browser and have live previews; the review action accepts PRs only.

Registration writes `profiles` or `default_profile` to the existing private
`config.local.json` overlay with mode 0600. It preserves other sections and the
public config. Duplicate names, unknown accounts/defaults, command-name
collisions, and unsafe paths fail visibly. Concurrent registrations use an
exclusive lock and revalidate file preimages before atomic replacement. A
`PROFILE_SAVE_UNVERIFIED` outcome requires inspecting the overlay before retrying.

## Native GitHub cache and freshness

Lists reuse `gh api --cache 60s`; GitHub CLI owns storage and includes the host,
account token, and full query in its cache key. No separate object store or
background synchronizer runs. The panel and JSON identify the selected profile
and requested cache policy. Identity checks, previews, and review checkout stay
uncached, so a list entry cannot authorize checkout of an unverified head.

`work --refresh`, `work list --refresh`, and Ctrl-R fetch live lists. Bypassing
the cache does not replace an older CLI cache entry: a later ordinary launch
can reuse it until its original TTL expires. The tool does not clear other
GitHub CLI caches. Cache payloads are private local response data managed by gh.

Version 2 public config adds `profiles`, `default_profile`, and `cache`; exact
version 1 configurations remain readable with inbox and the 60-second policy.
Customize the policy in the private overlay:

```json
{
  "type": "WORK_CONFIG_LOCAL",
  "cache": {"type": "GH_API", "ttl_seconds": 120}
}
```

TTL accepts 1–3600 seconds. Use `{"type":"DISABLED"}` as the cache value to
always fetch live. Profiles have this exact shape:

```json
{
  "type": "GITHUB_SEARCH",
  "name": "triage",
  "query": "repo:org/repo is:open is:pr label:bug",
  "accounts": []
}
```

Profiles in the local overlay replace the public profile list; the built-in
inbox is always available. Advanced Boolean query syntax depends on the host.
GitHub App user tokens can require an explicit `is:pr` or `is:issue` qualifier.
The parser accepts the legacy search envelope and current explicit lexical
responses; semantic/hybrid modes and unknown envelope fields fail visibly.
Search contract failures distinguish envelope, item, reference, timestamp,
and draft evidence without logging response payloads.

PR descriptions, checks, and review decisions load only for the selected preview.
Unrecognized check states fail visibly; an absent check or review signal remains
`UNVERIFIED`. The list is limited to 50 results per account and queue, and 50 Jira
tickets. GitHub sources expose complete or limited coverage with the provider's
total count; incomplete API searches fail visibly. A failed source stays visible alongside successful sources. `work list
--json` exits nonzero if any source failed, even when other sources returned items.

## Accounts and configuration

Use `work setup` for setup guidance, or authenticate directly:

```sh
gh auth login --hostname github.com
gh auth login --hostname your-enterprise-host
acli jira auth login --web
work accounts
```

`work accounts` prints only account names, hostnames, and logins. An empty public
account list discovers available identities through `gh auth status --json hosts`.
To give accounts stable names and map repositories, create the untracked
`~/.config/work/config.local.json`:

```json
{
  "type": "WORK_CONFIG_LOCAL",
  "accounts": [
    {"name": "personal", "hostname": "github.com", "login": "your-login"},
    {"name": "cloud", "hostname": "your-tenant.ghe.com", "login": "your-cloud-login"},
    {"name": "enterprise", "hostname": "git.example.com", "login": "your-enterprise-login"}
  ],
  "repositories": [
    {"hostname": "github.com", "owner": "your-owner", "name": "your-repo", "path": "~/code/your-repo"}
  ],
  "jira": {"project": "TEAM"}
}
```

These identities are examples, not configured accounts. The overlay replaces each
supplied section of the public `WORK_CONFIG` document; unknown fields, duplicate
account names or identities, and duplicate repository mappings are rejected.
Logins accept letters, digits, hyphens, and underscores for enterprise identities.
Tokens are excluded from this contract. The public editor defaults to `nvim`.
Set `WORK_CONFIG` or use `work --config FILE ...` to select another complete
configuration. `WORK_STATE_DIR` or `--state-dir` selects another private state root.

Every GitHub operation obtains a token for the exact hostname/login, clears ambient
GitHub credentials, scopes the child process, and verifies the authenticated login.
Cloud hosts (`github.com` and `*.ghe.com`) use `GH_TOKEN`; other GitHub hosts use
`GH_ENTERPRISE_TOKEN`. Tokens are never printed, logged, or persisted. This workflow
never calls `gh auth switch` or changes the globally active account.

Select an optional default review account with `work accounts NAME`. This writes
only the work tool's private selection. The panel still shows all configured
accounts. `--account NAME` overrides the selection for a review.

## Detached reviews

```sh
work review https://github.com/owner/repo/pull/123 --account personal
work review 123 --account enterprise --repo ~/code/enterprise-repo
work cleanup SESSION_ID
```

The repository must have an `origin` matching the PR's hostname and repository.
A URL uses its repository mapping or the current repository; a number uses the
current repository or `--repo`. Multiple logins for one host require a selected
or explicit account. Review creation uses installed GitHub CLI support for
`gh pr checkout --detach --worktree`, verifies the exact head and Git registration,
then opens the configured editor. After a successful editor exit, it attempts
clean-only owned cleanup. Edits, extra files, new commits, editor failures, or
uncertain ownership preserve the session and print its ID and typed reason.
It creates no temporary branch. `--no-editor` retains the worktree for explicit
management; `--json` emits a `WORK_REVIEW` result with a `REVIEW_REMOVED`,
`REVIEW_MANAGED`, or `REVIEW_PRESERVED` lifecycle.

The private state root defaults to `~/.local/state/shell-config/work`. A unique
session owns a path under `reviews/`, a `WORK_REVIEW_SESSION` record under
`sessions/`, and a matching ownership marker in that worktree's Git administrative
directory. Cleanup verifies all three, the original shared repository and detached
HEAD, and Git's worktree registration. It refuses changed HEADs, tracked edits,
staged changes, untracked or ignored files, and index flags that hide edits.
It never forces removal and does not run repository hooks. Preserved and explicitly
managed worktrees remain until you clean them up.

`CREATE_UNVERIFIED` during review creation preserves any partially created path.
Inspect the reported session path under the state root before recovering manually;
the command does not guess ownership or delete partial state automatically.

## Jira creation and output

`work new` prompts for the project, summary, and type, opens your configured editor
for the description, then shows the result and asks before sending it to Jira.
`work new --project TEAM --summary 'A task'` explicitly requests creation and still
opens the description editor. Add `--dry-run` to print the command without creating
a ticket. Creation uses official ACLI `--description-file` and `--json`. A successful
result must contain a valid key in the requested project and a positive string ID;
if supplied, its `self` URL must identify that same Jira issue. The tool emits only
the refined `JIRA_CREATED` result. `CREATE_UNVERIFIED` means creation may have
happened; inspect Jira before retrying.

JSON list output is a `WORK_SNAPSHOT` with `PULL_REQUEST`, `GITHUB_ISSUE`, or `JIRA_TICKET` items and
`SOURCE_SUCCESS` or `SOURCE_FAILURE` records. Failures carry `WORK_FAILURE` data
with finite `stage`, `reason`, and sanitized `context`; raw provider errors stay
private. Account output is `WORK_ACCOUNTS`; local selection is
`WORK_ACCOUNT_SELECTION`. Unsupported provider JSON fails rather than becoming
an empty successful result.

The fixture tests exercise isolated Cloud and Server account contexts, on-demand
preview, editor-backed ticket creation, and actual disposable Git worktrees.
Live Jira reads and creation still require an authenticated ACLI account; fixture
evidence does not establish live Jira success.

```sh
python3 tests/work.py
python3 tests/work-completion.py
```

References: [GitHub CLI environment](https://cli.github.com/manual/gh_help_environment),
[PR checkout](https://cli.github.com/manual/gh_pr_checkout),
[ACLI work item search](https://developer.atlassian.com/cloud/acli/reference/commands/jira-workitem-search/),
and [ACLI creation](https://developer.atlassian.com/cloud/acli/reference/commands/jira-workitem-create/).
