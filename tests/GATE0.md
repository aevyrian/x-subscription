# Gate 0: Legacy characterization

Run `python tests/run_gate0.py` from the workspace root.
This is the L1–L4 gate. Tests use temporary synthetic storage and mock the
download subprocess. The API tests bind only to ephemeral `127.0.0.1`.
The runner explicitly allowlists safe backend/scheduler and characterization
modules. It excludes `tests/test_metube_config.py`, whose old checks read
an ignored deployment config, so it is not safe for this gate.

Contracts: C1 subscriptions; C2 jobs; C3 downloader results; C4 archive
config reuse; C5 media directory and preservation; C6 timeline/detail;
C7 comments; C8 synthetic X cookie state; C9 restart recovery; C10 no full
refetch on incremental jobs; C11 whole-site authentication.

Known legacy issues captured as passing tests:

- DEC-019 is fixed: editing frequency/window recomputes `.next_<id>`.
- DEC-020: a running media job without a matching trigger becomes failed on restart.
- Running comment jobs do not use media trigger recovery; queued comment jobs
  are not recovered into the media trigger queue.
- No-new succeeds as `done` with zero new counts, without a `changed` field.

Quarantined from this gate: `tests/browser/test_final.js` (old selectors and
403 assumption), `tests/browser/test_video.js` (unreliable login setup), and
`local-dev/check_page.py` (false-green exit behavior). Browser E2E is not
expanded in Gate 0.
