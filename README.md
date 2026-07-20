# git-dr-mirror

**One-way disaster-recovery mirror of your GitHub repositories to GitLab.**

Every run discovers all of your GitHub repositories (public _and_ private),
mirrors them into a local cache of bare repositories, and pushes the complete
Git history — all branches and tags — to projects in a GitLab group.

If GitHub disappears tomorrow, you have two full copies of everything:
one on disk, one on GitLab.

```text
GitHub (source of truth)
   │  discover via API + git clone/fetch
   ▼
Local cache of bare mirrors        e.g. ~/github-backup/<repo>.git
   │  git push (branches + tags)
   ▼
GitLab group (backup)              e.g. gitlab.com/<your-group>/<repo>
```

## Design principles

- **One-way, append-only.** GitHub is the only source of truth. Nothing is
  ever pushed back to GitHub, and _nothing is ever deleted from GitLab_ —
  the codebase contains no DELETE call (a test enforces this). A repository
  deleted on GitHub simply stops being updated; its backup remains.
- **Idempotent.** Running twice changes nothing the second time. Safe to
  interrupt at any point (Ctrl-C, crash, power loss) and rerun.
- **Fault tolerant.** One repository failing never stops the others.
  Transient API errors are retried with backoff, git operations have hard
  timeouts and a retry, interrupted clones are detected and redone, and
  overlapping runs are prevented by a kernel-managed lock.
- **Secrets stay secret.** Tokens are read from the environment (or a
  `.env` file) and injected into git per-invocation. They never appear in
  command lines, `ps` output, git remote config, logs, or on disk.

## What is backed up

| Backed up           | Ignored                                        |
| ------------------- | ---------------------------------------------- |
| Full commit history | Issues, pull requests, discussions             |
| All branches        | Releases (the tags themselves _are_ backed up) |
| All tags            | GitHub Actions, stars, watchers, wikis         |

Scope: repositories **you own**, excluding forks, by default — both
configurable (see [Configuration](#configuration)).

## Requirements

- Linux (any distribution; uses `flock`)
- Python ≥ 3.13 — with [uv](https://docs.astral.sh/uv/) _or_ plain
  `python3` + pip/venv; the installer auto-detects and uses what you have
- `git` on `PATH`
- systemd for scheduled runs (optional — a cron alternative is documented)
- A GitHub token and a GitLab token (scopes below)

## Quick start

```bash
git clone https://github.com/NWGKGIT/git-dr-mirror
cd git-dr-mirror
./install.sh            # venv + install + systemd timer, one command

$EDITOR .env            # fill in GITHUB_TOKEN, GITLAB_TOKEN, GITLAB_GROUP

# See what would happen, without touching anything:
./.venv/bin/git-dr-mirror --dry-run

# Test end-to-end with a single repository first:
./.venv/bin/git-dr-mirror --repo some-small-repo

# Full run:
./.venv/bin/git-dr-mirror
```

`install.sh` is safe to rerun anytime (it's idempotent) and never
overwrites an existing `.env`. To remove the scheduled units again:
`./install.sh --uninstall` — code, `.env`, and mirrors stay put.

### Creating the tokens

**GitHub** — <https://github.com/settings/personal-access-tokens>
(fine-grained token, recommended):

- Repository access: _All repositories_
- Permissions: **Contents: Read-only**, **Metadata: Read-only**

Or a classic token with the `repo` scope.

**GitLab** — _Preferences → Access tokens_:

- Scopes: **`api`** (create projects) and **`write_repository`** (push)
- No delete/admin permissions are needed — the tool never deletes anything.

**GitLab group** — create a group once in the GitLab UI (e.g.
`your-github-backup`) and put its path in `GITLAB_GROUP`. Projects inside
it are created automatically; the group itself is not, so the token's blast
radius stays small.

## Configuration

Everything is an environment variable; a `.env` file in the working
directory is loaded automatically (real environment variables win).
`.env.example` is the fully commented reference.

| Variable             | Default                  | Purpose                                                             |
| -------------------- | ------------------------ | ------------------------------------------------------------------- |
| `GITHUB_TOKEN`       | **required**             | Read access to your repositories                                    |
| `GITLAB_TOKEN`       | **required**             | Create projects + push                                              |
| `GITLAB_GROUP`       | **required**             | Existing GitLab group for the backups (subgroups OK: `team/backup`) |
| `GITLAB_URL`         | `https://gitlab.com`     | Self-hosted GitLab supported                                        |
| `GITHUB_API_URL`     | `https://api.github.com` | GitHub Enterprise supported                                         |
| `MIRROR_DIR`         | `~/github-backup`        | Local bare-mirror cache                                             |
| `GITHUB_AFFILIATION` | `owner`                  | Widen scope: `owner,collaborator,organization_member`               |
| `INCLUDE_FORKS`      | `false`                  | Also mirror forks                                                   |
| `EXCLUDE_REPOS`      | _(empty)_                | Comma-separated name globs to skip, e.g. `scratch-*,tmp`            |
| `GITLAB_VISIBILITY`  | `private`                | Visibility of created projects                                      |
| `HTTP_TIMEOUT`       | `30`                     | Seconds per API request                                             |
| `HTTP_RETRIES`       | `3`                      | Retries (exponential backoff) for transient API errors              |
| `GIT_TIMEOUT`        | `3600`                   | Seconds per git operation — raise for huge repos                    |
| `LOG_LEVEL`          | `INFO`                   | `DEBUG`, `INFO`, `WARNING`, `ERROR`                                 |

### CLI flags

```text
git-dr-mirror [--dry-run] [--repo PATTERN] [--env-file PATH] [--version]
```

- `--dry-run` — discover and report; change nothing anywhere.
- `--repo PATTERN` — process only repositories matching the glob.
- `--env-file PATH` — read configuration from a specific dotenv file.

Exit codes: `0` all good (also when another run already holds the lock),
`1` at least one repository failed, `2` configuration error, `130`
interrupted.

## Deployment

`./install.sh` sets up everything: a systemd **user** timer runs the
backup every 6 hours, wherever you cloned the repo. The same setup works
on a laptop and on an always-on VPS — the only difference is one command
(see below).

What the installer configures:

- `~/.config/systemd/user/git-dr-mirror.{service,timer}`, generated with
  the absolute path of your clone — no editing needed.
- `Persistent=true` in the timer: if the machine was shut down or asleep
  when a run was due, systemd fires the missed run once, shortly after
  the next boot. Runs never pile up; a long-powered-off laptop just
  quietly catches up.
- The service runs with `Nice=10` and idle I/O priority, so a backup
  never makes the machine feel slow.

```bash
systemctl --user list-timers git-dr-mirror.timer   # next/last run
systemctl --user start git-dr-mirror.service       # run once now
journalctl --user -u git-dr-mirror -f              # logs
```

To change the schedule (default `OnCalendar=00/6:17:00` = every six
hours), use a drop-in override — it survives reruns of `install.sh`,
which regenerate the base unit files:

```bash
systemctl --user edit git-dr-mirror.timer
```

and enter, e.g. for daily at 03:17:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 03:17:00
```

(The empty `OnCalendar=` line clears the default before setting yours.)

### Running on a VPS

Identical: clone, `./install.sh`, done. Just make sure user services keep
running after you disconnect:

```bash
loginctl enable-linger $USER
```

(The installer reminds you if lingering is off.) For extra isolation you
can create a dedicated user account and install under it — nothing in the
tool requires your main account or root.

### Alternative: cron (no systemd)

```cron
# m  h        dom mon dow  command
17 */6 * * *  cd /path/to/git-dr-mirror && ./.venv/bin/git-dr-mirror >> ~/.local/state/git-dr-mirror.log 2>&1
```

Note that plain cron has no equivalent of `Persistent=true` — runs missed
while the machine was off are skipped until the next scheduled slot
(`anacron` can fill that gap). The tool itself doesn't care: any run always
brings the backup fully up to date, however long the machine was off.

## Failure handling & recovery

Scenarios this tool is designed to survive:

- **Machine powered off for days/weeks** → next boot triggers one catch-up
  run (`Persistent=true`); the run transfers whatever changed in the
  meantime. Nothing special happens, by design.
- **Run interrupted mid-clone** (Ctrl-C, crash, power loss) → the
  half-written directory fails a health check on the next run, is
  quarantined as `<repo>.git.corrupt` (never deleted), and the repository
  is recloned fresh.
- **One repository fails** (auth, network, GitLab hiccup) → logged, the
  remaining repositories still run, the process exits non-zero so
  systemd/cron mark the run failed. Check `journalctl` for the `FAILED`
  lines.
- **Two runs at once** (manual + timer) → the second exits immediately and
  cleanly; a kernel `flock` guarantees a crashed holder can't leave a stale
  lock behind.
- **Transient API failures / rate limits** → retried with exponential
  backoff, honoring `Retry-After`.
- **Hung network** → every API call and git operation has a hard timeout.

## Restoring from the backup

Each GitLab project is a complete mirror of the GitHub repository at the
last successful run — full history, all branches, all tags.

```bash
# Just get the code back:
git clone https://gitlab.com/<group>/<repo>.git

# Recreate the repo on GitHub (or anywhere):
git clone --mirror https://gitlab.com/<group>/<repo>.git
cd <repo>.git
git push --mirror https://github.com/<you>/<new-repo>.git
```

The local cache (`MIRROR_DIR`) contains the same data as bare repositories
and works offline: `git clone ~/github-backup/<repo>.git`.

## FAQ

**Why doesn't it delete GitLab projects when I delete a GitHub repo?**
That's the point of disaster recovery: deletion (accidental or malicious)
is one of the disasters. Deleted repos stop updating but their backups
survive. Clean them up manually if you truly want them gone.

**Are deleted _branches_ also kept?**
No — branch and tag deletions on GitHub propagate to the mirror on the next
run. The mirror reflects the current state of the repository's refs; the
no-delete guarantee applies to repositories/projects, not individual refs.
(History reachable from remaining refs is always intact.)

**Why must the GitLab group already exist?**
Creating top-level groups is restricted on gitlab.com, and requiring an
existing group keeps the token's permissions minimal and the blast radius
small.

**Does it push GitHub pull-request refs (`refs/pull/*`)?**
No. Only branches and tags are pushed — GitLab rejects writes to hidden
refs, and PR refs are GitHub-internal. They _are_ kept in the local mirror
cache.

**My repo name is valid on GitHub but the GitLab project has a suffix — why?**
GitLab project paths are stricter than GitHub repo names: they can't start
or end with `-`, `_` or `.`, and can't end in `.git` or `.atom`. When a
name breaks those rules (e.g. `myrepo-`), the tool derives a valid path by
cleaning the name and appending a short hash of the original
(`myrepo-6bff2b2`) so two different GitHub repos can never collide on the
same GitLab project. The project's _display name_ keeps the original repo
name.

**What about GitHub wikis?**
Not currently backed up (a wiki is a separate `<repo>.wiki.git`
repository). PRs welcome.

**Private repos on GitHub become what on GitLab?**
`private` projects (default `GITLAB_VISIBILITY`). Public repos also become
private on GitLab unless you change that — a backup has no reason to be
public.

## Troubleshooting

- `Configuration error: Missing required configuration: ...` — set the
  variable in `.env` (same directory you run from) or the environment.
- `GitLab group '...' does not exist` — create the group in the GitLab UI,
  or fix `GITLAB_GROUP`; also check the token can see the group.
- `git clone/push failed ... HTTP 401/403` — token expired or missing a
  scope (GitHub: Contents read; GitLab: `write_repository` + `api`).
- `Another backup run is in progress` — fine; the other run finishes the
  job. If you're sure nothing is running, the lock cannot be stale (it dies
  with its process) — look for a live process: `pgrep -af git-dr-mirror`.
- A repo keeps failing while others work — run it alone with
  `LOG_LEVEL=DEBUG ./.venv/bin/git-dr-mirror --repo <name>` and read the output.
- Timer didn't fire after boot — check `systemctl --user list-timers` and
  that lingering is enabled (`loginctl enable-linger $USER`) if you weren't
  logged in.

## Development

```bash
./install.sh      # or: uv sync / pip install -e .
uv run pytest     # run the test suite (or: ./.venv/bin/pytest)
```

The test suite mocks all HTTP and git subprocess calls; it never touches
GitHub, GitLab, or your mirrors.

Layout: `src/git_dr_mirror/` — `config` (env parsing), `github` (discovery),
`gitlab` (project ensure), `mirror` (git operations), `lock` (single-run
flock), `runner` (orchestration), `cli` (entry point).

## Heads Up

One heads-up: renaming a group path changes its URL, so if anything
else referenced gitlab.com/backup-group3, that link is now dead
(GitLab may redirect for a while, but don't rely on it).

## License

MIT
