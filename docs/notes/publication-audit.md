# Publication audit

The audit performed before the repository was proposed for public release, and the checks that now
run on every commit so it stays true.

Audited 2026-09-11 at commit `678a358` plus the Phase 6 polish.

## What runs automatically

`tests/test_publication_hygiene.py` fails the build on anything private entering the repository:

| Check | What it prevents |
|---|---|
| tracked-file inventory | build output, `node_modules`, `dist`, `.venv`, databases, caches, Playwright artifacts |
| `.env` | any dotenv file other than `.env.example` |
| images | any tracked image other than the five portfolio screenshots |
| credential shapes | Anthropic, OpenAI, GitHub, AWS, Slack, Google keys, private key blocks, bearer tokens, connection strings |
| synthetic-sample check | the redaction fixtures must look obviously fake, so a real key cannot hide among them |
| personal information | Windows and POSIX home paths, email addresses |
| provider reasoning | `thinking` or `encrypted_content` payloads in any tracked file |
| unsafe rendering | `dangerouslySetInnerHTML` anywhere in the frontend |
| CORS | a wildcard origin in any form |
| history | any path that was added and later removed, which is where deleted secrets hide |

`tests/test_readme_claims.py` re-derives every number the README states from the code, the generated
world and the frozen benchmark, and fails if the README drifts or starts overclaiming.

## The manual audit

**Tracked files.** 267 files: Python, Markdown, JSON, TypeScript, CSS, one YAML workflow, one TOML
manifest, one SVG, five PNG screenshots. No build output, no virtual environment, no database, no
credential file.

**Git history.** 19 commits, 305 blobs, 262 distinct paths ever added and 262 still tracked: nothing
has ever been committed and later deleted, so there is no deleted-secret risk. Every historical blob
was scanned for Anthropic, OpenAI, GitHub, AWS, Slack and Google credential shapes, private key
blocks, bearer tokens, database connection strings, `Authorization` headers, Windows and POSIX home
paths and email addresses. No real credential, path or address in any of them.

**Secret scanning.** `detect-secrets` (27 plugins) over all 262 tracked files reported four findings,
all reviewed:

| Finding | Verdict |
|---|---|
| `tests/test_anthropic_adapter.py:191` | an Anthropic-shaped string reading `...test-not-real-0000...`, the fixture proving a missing-SDK error is raised |
| `tests/test_openai_adapter.py:177` | an OpenAI-shaped string reading `...test-not-real-0000...`, same purpose |
| `tests/test_replay_and_trace.py:64` | a GitHub-shaped string of sequential alphabet characters, asserting the redactor masks it |
| `tests/test_replay_and_trace.py:118` | an OpenAI-shaped string of sequential alphabet characters, asserting the trace store masks it |

The samples are described rather than quoted here, because quoting them would put credential-shaped
strings in a document that is not one of the four files allowed to contain them, and the automated
check would rightly fail. It did, which is the check working.

All four are synthetic and exist to prove the redaction layer works. The automated test above keeps
them contained to those files and requires them to remain obviously fake.

**Screenshots.** All five are 2880x1800 PNGs captured from the running application by
`npm run screenshots`. PNG chunk inspection found `IHDR`, `IDAT` and `IEND` only: no `tEXt`, `iTXt`,
`zTXt` or `eXIf`, so no capture tool, username, path or timestamp is embedded. Each was reviewed by
eye for visible content: synthetic company data only, no credentials, no local paths, no browser
chrome, no cursor over content, no partially loaded state and no debugging banners.

**Licensing.** MIT, with the full text in `LICENSE`. No third-party source is vendored into `src/` or
`web/src/`; every dependency is a normal, unmodified package from PyPI or npm. No font files, icons
or images from third parties are tracked: the interface uses a system font stack and inline SVG
drawn for this project. The screenshots contain no third-party branding.

**Synthetic data.** Everything describes a fictional company generated from a fixed seed. Service
names, host names, incident text, runbooks, log lines and metric series are produced by
`signalforge.world`; none was copied from a real system, and the ground-truth firewall keeps the
scenario answers out of everything the investigator can reach.
