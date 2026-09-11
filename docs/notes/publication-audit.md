# Publication audit

The audit performed before the repository was proposed for public release, and the checks that now
run on every commit so it stays true.

Audited 2026-09-11, over the full Phase 6 tree. Commit hashes from before the history rewrite
described below no longer exist, so this note cites none.

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

## History rewrite before publication

One directory of internal planning documents was removed from the repository **and from every
commit in its history**, before the project was ever public. Those four documents were written for
an audience of one: they compared SignalForge against two other unpublished projects, quoted those
projects' record and test counts, and described the intended reader and the release milestone in
terms that framed the whole thing as an exercise rather than a system.

Nothing in them was a secret, and none of it was customer or employer data. They were removed
because they said things about the project, and about work unrelated to it, that a public reader has
no reason to see, and because deleting them in a new commit would have left them fully recoverable
from history.

The rewrite used `git-filter-repo` 2.47.0 while the repository was still private, so the documents
were never published in any form. Every commit hash therefore changed; the content of the working
tree did not, apart from this note.

Verification after the rewrite:

- no commit in the published history touches the removed path;
- no object reachable from it carries one of those documents;
- every blob in the rewritten history was re-scanned for the document titles and for the names of the
  unrelated projects the planning notes discussed, and neither survives in any document body;
- the engineering documentation a reader actually needs is untouched and lives in `docs/notes/`.

A second pass then removed the remaining references to that material from older revisions of files
that survive. Four replacements were made, each one an exact string identified in advance and
verified to match only the intended historical blobs:

| Where | What changed |
|---|---|
| five earlier `README.md` revisions | a phase-table cell pointing at the removed directory, and one sentence describing the intended reader |
| one earlier `docs/notes/api-boundary.md` revision | a sentence naming an unrelated project in an aside about framework choice |
| one earlier revision of this audit | the closing section that discussed the removal as an open question |

Seven historical blobs changed in total. No file in the current tree was touched: the tree object at
the head of the rewritten history is the same object as before the pass, so every current document,
test, screenshot and source file is byte-for-byte unchanged. Where a replacement would have left a
sentence dangling, the whole sentence was rewritten to a neutral engineering equivalent rather than
being cut. Ordinary words were left alone; only the specific planning sentences were touched.

The temporary local backup branch was itself rewritten by `git-filter-repo`, which rewrites every
ref in the repository, so it was deleted rather than relied on. The safety net during the operation
was the remote, which still held the original history until the force-push, and a bundle held outside
the repository.
