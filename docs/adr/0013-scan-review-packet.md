# ADR-0013 — Save scan results as a review packet

**Status:** Accepted (2026-06-22)

## Context

A Wingman scan produced a prioritized queue in the side panel, but the result
disappeared with the browser session. There was no file for a second reviewer or a
later session, and no concise explanation of which findings Wingman could fix.

The saved output must also avoid copying client values or raw workbook identifiers
into a portable file.

## Decision

Add `review_packet.py`, a pure-logic module that converts a sheet or workbook scan
into JSON and Markdown.

### Finding status

Each finding receives one of three statuses:

- **ACCEPT**: Wingman has an automatic fix with readback and restore behavior.
- **BLOCKED**: the issue has a known next step, but it requires a person, a source
  document, or a Workiva write that Wingman will not make automatically.
- **UNVERIFIED**: the detector found a possible issue, but someone must confirm it in
  the live workbook or an export before changing anything.

`classify_disposition` derives the status from `fix_lane`, `fixable`, check results,
and diagnosis fields. `_BLOCKED_KINDS` handles the two exceptions, `broken-ref` and
`scan-error`. The packet also records the capability tier, the reason for the status,
and the available restore path.

The packet's `overall` field uses the most restrictive finding status. `clientReady`
is always false because a scan alone does not verify rendering, links, or delivery.

### Redaction

- The raw spreadsheet ID is replaced with a SHA-256 `workbookHash`.
- Cell values are omitted.
- Numeric runs in free-text signatures are replaced with `#`; hexadecimal color
  values remain readable.
- Sheet names and a limited number of A1 cell addresses remain because a reviewer
  needs them to locate the issue.

This is intentionally different from the bulk activity log, which removes more
location data. Review packets are meant to stay in the client's own working context.

### Files and route

`render_packet_md` creates the Markdown version. `write_packet` saves JSON and
Markdown under `WINGMAN_PACKET_DIR`, or `~/.wingman/packets` by default, outside the
repository.

`GET` or `POST /api/review-packet` accepts `spreadsheetId`, optional `sheetId`,
`checks`, `label`, and `write` fields. It reuses the read-only `/api/queue` scan path.
The only optional side effect is writing the local packet files; it does not write to
Workiva or create an external task.

## Consequences

- Scan results can be reviewed after the browser session ends.
- The packet explains what Wingman can fix and what still needs a person.
- Creating a task in an external system remains a separate, explicit action.
- The packet is useful for review but is not evidence that a workbook is ready to
  publish.

## Alternatives considered

- **Reuse the activity-log redaction.** Rejected because removing all cell addresses
  would make the packet difficult to act on.
- **Include the full diagnosis.** Deferred because guided steps may contain client
  search targets and need a separate redaction review.
- **Create the external task automatically.** Rejected because that would send data
  outside Wingman without a separate user action.

## Verification

- `python3 server/review_packet.py`: 28/28 self-tests passed.
- `python3 -m pytest server/ -q`: 394 tests passed, including 31 packet tests and two
  route tests.
- `node extension/content.test.js`: 222 tests passed.

The route test replaces the scan function, so it makes no live Workiva call or write.
