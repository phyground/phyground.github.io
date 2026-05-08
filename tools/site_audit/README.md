# Site Audit Harness

Tools for auditing the rendered phyground.github.io site. The harness has
two complementary drivers:

- `run_audit.py` — runtime audit. Loads each page in a real Chromium via
  Playwright, captures console errors, failed network requests, the
  post-redirect URL, the main-document HTTP status, and a viewport-sized
  PNG screenshot. Supports two sources: a localhost rebuild (`--target
  local`) and the published user-fork URL (`--target fork`).
- `structural_audit.py` — the structural auditor; see `structural_audit.py`
  (added in a follow-up task). Reads on-disk HTML produced by
  `tools/build_site.py` to verify links, asset references, and the
  expected page set without launching a browser.

This README documents `run_audit.py`. The two drivers share the
`AuditRecord` schema exposed at the package level
(`from tools.site_audit import AuditRecord`).

## Install

The runtime driver depends on Playwright. Install it from the pinned
sibling requirements file at the repo root:

```bash
pip install -r requirements-audit.txt
python -m playwright install chromium
```

`requirements-audit.txt` is intentionally separate from the main
`requirements.txt` so the static-site build (`tools/build_site.py`) and
the existing pytest contracts do not pull in Playwright.

## Invocation

Both forms are supported:

```bash
# As a script (works from the repo root):
python tools/site_audit/run_audit.py --help

# As a module (uses the package layout):
python -m tools.site_audit.run_audit --help
```

### Required flags

| Flag         | Description                                                                      |
|--------------|----------------------------------------------------------------------------------|
| `--target`   | `local` (serve the repo over localhost) or `fork` (the user-fork URL).           |
| `--urls`     | Path to a text file with one relative URL per line (e.g. `/`, `/about/`).         |

### Optional flags

| Flag         | Default                                            | Description                                                |
|--------------|----------------------------------------------------|------------------------------------------------------------|
| `--out`      | `.audit_artifacts/current/<target>/` (repo root)   | Directory for `records.json` and PNG screenshots.          |
| `--viewport` | `1280x800`                                         | Browser viewport, parsed as `<width>x<height>`.            |
| `--dry-run`  | off                                                | Skip the HTTP server and Playwright; emit skeleton records. |

The `--target local` driver binds a free port via `http.server` and
serves the repo root, then prefixes each URL with `http://127.0.0.1:<port>`.
The `--target fork` driver prefixes each URL with
`https://lukelin-web.github.io/phyground.github.io`. URL joining always
uses `prefix.rstrip("/") + url` so a URL like `/about/` produces exactly
one slash at the boundary.

### Example

```bash
cat > /tmp/urls.txt <<'EOF'
/
/about/
/leaderboard/
EOF

python tools/site_audit/run_audit.py \
  --target local \
  --urls /tmp/urls.txt \
  --out .audit_artifacts/quick/local/
```

## Artifact layout

```
.audit_artifacts/<round>/<target>/
  records.json        # JSON array; one entry per URL (schema below)
  root.png            # screenshot for "/"
  about.png           # screenshot for "/about/"
  leaderboard.png     # screenshot for "/leaderboard/"
  ...
```

Screenshot stems are derived from the relative URL: `/` becomes `root`,
and other paths replace `/` with `_` after stripping leading and
trailing separators.

## Record schema

Each entry in `records.json` is a JSON object with the following keys
(matching `tools.site_audit.AuditRecord`):

| Key                    | Type            | Notes                                                                 |
|------------------------|-----------------|-----------------------------------------------------------------------|
| `url`                  | string          | The original relative path from `--urls`.                            |
| `prefixed_url`         | string          | The absolute URL the auditor would fetch (or did fetch).             |
| `target`               | `local`/`fork`  | Source mode passed to `--target`.                                    |
| `final_url`            | string \| null  | Post-redirect URL; `null` in dry-run.                                |
| `http_status`          | int \| null     | Main-document HTTP status; `null` in dry-run.                        |
| `viewport`             | string          | Echo of `--viewport`, e.g. `"1280x800"`.                             |
| `console_error_count`  | int             | Number of `console.error` events; `0` in dry-run.                    |
| `failed_request_count` | int             | Number of failed/4xx/5xx network requests; `0` in dry-run.            |
| `screenshot_path`      | string          | Path to the PNG (would-be path in dry-run).                          |
| `console_errors`       | list[object]    | Each entry has `text` and `location`; empty in dry-run.              |
| `failed_requests`      | list[object]    | Each entry has `url`, `status`, `failure`; empty in dry-run.         |

## `--dry-run` mode

`--dry-run` is the test fixture's primary entry point. It:

- skips the localhost HTTP server and Playwright entirely;
- still reads `--urls` and validates `--target` and `--viewport`;
- emits one skeleton record per URL with `final_url=null`,
  `http_status=null`, `console_error_count=0`, `failed_request_count=0`,
  empty `console_errors`/`failed_requests`, and the
  *would-be* `screenshot_path`;
- writes `records.json` to `--out` so the JSON schema and CLI surface
  can be tested without a browser.

The pytest suite under `tests/test_site_audit_harness.py` uses this
mode to lock in the schema; the real capture path is exercised in later
rounds when Playwright is installed.
