# Site Audit Harness

Tools for auditing the rendered phyground.github.io site. The harness has
two complementary drivers:

- `run_audit.py` — runtime audit. Loads each page in a real Chromium via
  Playwright, captures console errors, failed network requests, the
  post-redirect URL, the main-document HTTP status, and a viewport-sized
  PNG screenshot. Supports two sources: a localhost rebuild (`--target
  local`) and the published user-fork URL (`--target fork`).
- `structural_audit.py` — the structural auditor. Reads on-disk HTML
  produced by `tools/build_site.py` (or any other source) to verify
  links and asset references without launching a browser. See the
  "Structural audit" section below.

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

Exactly one of the two URL-source flags below must be supplied; they
are mutually exclusive. Passing neither, or passing both, is a CLI error.

| Flag         | Description                                                                      |
|--------------|----------------------------------------------------------------------------------|
| `--urls`     | Path to a text file with one relative URL per line (e.g. `/`, `/about/`).         |
| `--url-set`  | Built-in canonical URL set. Currently only `repo` is accepted.                    |

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

### Canonical URL set (preferred)

`--url-set repo` is the canonical and preferred way to invoke an audit.
It resolves the 14-URL set deterministically from
`snapshot/index/site_config.json` so the audit cannot drift from the
live site state:

1. `/`
2. `/leaderboard/`
3. `/videos/`
4. `/about/`
5. `/videos/compare/` (placeholder compare state)
6. `/videos/compare/?prompt_id=<chosen_pid>` (populated compare state)
7-14. `/models/<key>/` for each of the 8 published models, sorted alphabetically.

`<chosen_pid>` is the alphabetically first prompt in
`site_config["prompts_index"]` whose `per_model_videos` AND
`per_model_scores` cover all 8 published models. The selection is
deterministic and stable across rebuilds; if no prompt has full
coverage the resolver raises `ValueError` rather than silently
auditing a partially-rendered compare state.

The same resolver is exposed as a debugging tool:

```bash
python -m tools.site_audit.url_set
```

which prints the 14 URLs one per line.

### Example

Preferred (canonical 14-URL audit set):

```bash
python tools/site_audit/run_audit.py \
  --target local \
  --url-set repo \
  --out .audit_artifacts/round-1/local/
```

Ad-hoc (custom URL list — useful for spot-checking a single page):

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
| `error`                | string \| null  | `null` on success; on per-URL capture failure, a short `"<ExcClass>: <msg>"` summary truncated to 500 chars. Always present. |

### Per-URL error isolation

A failing `page.goto()` (or any other exception while capturing a single
URL) does **not** abort the run. The driver:

- catches the exception, writes a short summary string to the record's
  `error` field (class name + colon + message, truncated to 500 chars),
- leaves `final_url` and `http_status` as `null`, `console_error_count`
  and `failed_request_count` as `0`, and the screenshot path pointing at
  the would-be PNG even if no PNG was written, and
- moves on to the next URL.

After every per-URL completion (success or failure) the driver atomically
rewrites `records.json` (write to `records.json.tmp`, then
`os.replace`). A mid-run abort therefore still leaves a usable evidence
file on disk: at most the in-flight URL is missing.

### Repeat runs

Pointing two consecutive runs at the same `--out` is supported. Before
each run the driver removes every top-level `*.png` file inside `--out`
(recursing into sub-directories is intentionally avoided so audit logs
or sibling artifacts under `<out>/logs/` survive). The cleanup count is
echoed to stderr as `[run_audit] cleaned N stale screenshot(s) from
<out>`. `records.json` is overwritten by the atomic incremental writes
described above; no manual cleanup is needed.

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

## Structural audit

`structural_audit.py` is a pure-Python auditor that walks one or more
on-disk HTML files, extracts every `href` / `src` reference from a
fixed set of tag/attribute pairs, and verifies that each *relative*
reference resolves to an existing file. Absolute URLs and
same-document anchors are catalogued separately and never trigger a
disk check.

It is implemented entirely against the standard library
(`html.parser`, `urllib.parse`, `pathlib`); importing
`tools.site_audit` or `tools.site_audit.structural_audit` does not
pull in Playwright.

### Invocation

```bash
# As a script (works from the repo root):
python tools/site_audit/structural_audit.py snapshot/index.html

# As a module (uses the package layout):
python -m tools.site_audit.structural_audit snapshot/about/index.html
```

Pass one or more HTML file paths positionally; each file must exist on
disk.

### Resolution rules

- A href starting with `/` resolves against `--repo-root` (default:
  the repo root containing `tools/`).
- Any other relative href resolves against the directory of the HTML
  file that contains it.
- Query strings and fragments are stripped before the on-disk check;
  `foo.css?v=2` and `foo.css#section` both resolve to `foo.css`.
- An empty `href=""` is treated as a self-link and recorded under
  `fragments`; it is never reported as broken.

### Inspected references

The auditor inspects the following `(tag, attribute)` pairs (also
exported as `STRUCTURAL_REF_ATTRIBUTES`):

`(a, href)`, `(link, href)`, `(script, src)`, `(img, src)`,
`(source, src)`, `(video, src)`, `(audio, src)`, `(iframe, src)`.

### Allow-prefix list

Any href starting with one of the following prefixes is treated as an
absolute reference and is *not* checked on disk (also exported as
`DEFAULT_ALLOW_PREFIXES`):

```
http://  https://  data:  mailto:  javascript:  #
```

Extend the list with one or more `--allow-prefix PREFIX` flags. The
default prefixes are always included.

### Optional flags

| Flag             | Default                                | Description                                            |
|------------------|----------------------------------------|--------------------------------------------------------|
| `--repo-root`    | parent of `tools/`                     | Resolution root for `/`-prefixed paths.                |
| `--allow-prefix` | (in addition to the defaults)          | Extra prefix to skip on-disk; repeat for more.         |
| `--report`       | (none; print summary only)             | Write a JSON report to this path.                      |

### JSON report shape

```json
{
  "audited": [
    {
      "file": "<absolute path to html file>",
      "broken": [
        {
          "original_href": "static/js/missing.js",
          "resolved_path": "<absolute path on disk>",
          "tag": "script",
          "attribute": "src"
        }
      ],
      "absolute": ["https://example.com"],
      "fragments": ["#main"]
    }
  ],
  "summary": {
    "total_refs": 12,
    "broken_refs": 1,
    "files_audited": 1
  }
}
```

`broken[*].resolved_path` is the *resolved on-disk path* (after
applying repo-root or file-relative resolution and stripping query /
fragment), not the raw href; the raw value is preserved as
`original_href`.

### Exit codes

| Code | Meaning                                                 |
|------|---------------------------------------------------------|
| `0`  | Every relative reference resolves on disk.              |
| `2`  | At least one relative reference is broken.              |

### Python API

For tests and in-process callers, the package exposes:

```python
from tools.site_audit import (
    audit_html_file,
    DEFAULT_ALLOW_PREFIXES,
    STRUCTURAL_REF_ATTRIBUTES,
    StructuralAuditResult,
    BrokenRef,
)

result = audit_html_file(html_path, repo_root=repo_root)
# result.file, result.broken, result.absolute, result.fragments,
# result.total_refs, result.broken_refs
```

### Known limits

- `srcset` (e.g. `<img srcset="a.png 1x, b.png 2x">`) is a
  comma-separated candidate list and is **not** inspected in this
  round; only plain `src` is checked. A future round may add a
  dedicated parser.
- The auditor only inspects the `(tag, attribute)` pairs listed
  above. References tucked inside inline CSS (`style="background:
  url(...)"`), `<meta>` redirects, or `<object data=...>` are out of
  scope for this round.
- The auditor reads HTML as UTF-8 with `errors="replace"` and tolerates
  malformed markup without raising; truly garbled documents may yield
  fewer refs than a real browser sees.
