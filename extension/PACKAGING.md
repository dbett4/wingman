# Wingman — private Chrome installation

The current route is a **privately paired unpacked extension**, not a store release
or enterprise force-install. Installation in the owner's browser requires approval.
The backend runs on davgent; the Mac runs only an encrypted SSH forward. See
[deployment controls and rollback](../deploy/README.md).

## Install the staged package

These steps assume the private service, tunnel and paired folder have already been
provisioned. Do not run a second backend on the Mac or regenerate its pairing token.

1. Open `chrome://extensions/` in the intended Chrome profile.
2. Check for an existing Wingman installation before adding another. Preserve any
   installation whose owner or source folder is unknown.
3. Use Developer mode and **Load unpacked**. Select the folder, not a ZIP:
   `~/Library/Application Support/Wingman/extension/`.
4. Open Wingman's **Details**. Confirm it is enabled, has a service worker and shows
   the expected source folder. Investigate manifest/runtime errors before continuing.
5. Keep that folder in place: Chrome loads directly from it. It contains a private
   pairing configuration; do not upload, share, paste or screenshot that file.

Developer mode was already enabled during the verified owner installation. Do not
change browser policies or unrelated extension settings to bypass an installation
failure. Store submission, hosting, registration fees and enterprise policies are
separate decisions, not prerequisites for this private route.

## Verify connection without opening Workiva

The commissioning backend intentionally has **no Workiva credentials or external
network egress**. Do not open a client workbook to test installation.

The local development candidate now includes **Connection & setup**. It opens on
first installation, through **Extension options**, or when a toolbar click cannot
reach a Workiva panel. Click **Check connection** there; no workbook or DevTools is
needed. The page separates service authorization, backend credential presence and
untested workbook access, and can copy redacted diagnostics. Updates do not open
this page automatically or interrupt a review. This candidate has been tested in
a disposable browser, not installed over the owner's commissioned Mac package.

Run the sanitized transport checker on the Mac:

```bash
python3 "$HOME/Library/Application Support/Wingman/check_connection.py" \
  --extension-config "$HOME/Library/Application Support/Wingman/extension/local-config.js"
```

That verifies the service and tunnel, not Chrome. Browser verification must also
use the installed worker: a paired `/api/connection` request should return 200 with
`authorization: accepted`, `serviceMode: read-only`, `workivaCredentials: missing`
and `workivaAccess: not_tested`. A wrong token and an authenticated malformed POST
to `/apply` must return 403; the latter must report `code: read_only`. Never print
the paired token while probing. Repeat after Wingman's **Reload** control.

**Observed September 15, 2026:** Chrome 153.0.8010.36 on the owner's Mac loaded the
staged folder through the normal file picker. The extension was enabled with zero
manifest/runtime errors. Worker-context connection and refusal probes passed both
before and after extension reload. No Workiva workbook was opened for these tests.
This is developer-assisted installation proof, not clean-machine onboarding,
in-page broker/content-script proof on Workiva, or product acceptance.

The earlier Chrome 149 automated-load failure was an observation from one spike;
its alleged universal content-script suppression was not established. Disposable
installed-extension tests now exercise the real worker and content scripts against
a fictional page. They do not establish compatibility with live Workiva.

## Permissions and data boundaries

- `storage` preserves local extension state; `alarms` drives the existing development
  reload check. The latter is not a supported package update mechanism.
- `debugger` supports cell navigation and screenshot capture. Chrome presents broad
  debugger/data-access warnings. The backend's read-only mode does **not** remove
  this browser permission or make all extension behavior read-only. Permission
  reduction remains an open product goal. Chrome explicitly excludes `debugger`
  from [optional permissions](https://developer.chrome.com/docs/extensions/reference/api/permissions).
  Removing it requires replacing or separating those features, not just moving
  the manifest entry to `optional_permissions`.
- Content scripts match the Workiva/wdesk hosts in `manifest.json`. Service requests
  use loopback HTTP, then travel through SSH to the private VPS. Do not claim that
  all data stays on the Mac. Provisioning Workiva access requires its own approval.
- Do not distribute `local-config.js`, `.env`, signing keys, client captures or
  runtime logs. The privately paired folder is not a publishable source bundle.

## Update, recover and remove

For an approved update, preserve the pairing file and previous verified package;
stage matching reviewed code and backend versions, then use Wingman's **Reload**
control. Repeat the connection/refusal checks. Restore the prior verified files and
reload to reverse a failed extension update. Upgrade/rollback have not yet passed
clean-machine product acceptance.

Disabling Wingman in Chrome stops the extension without deleting its private files.
Removing it through Chrome removes the browser installation, not the VPS service
or SSH tunnel. Those have separate rollback steps in the deployment guide. Do not
revive the retired Mac service during recovery.
