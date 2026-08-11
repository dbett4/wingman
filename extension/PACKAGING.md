# Wingman extension — packaging & install

This documents the **real install channels** that run the content script
fully (unlike `--load-extension` / CDP `Extensions.loadUnpacked`, which Chrome 149 loads but
**suppresses content-script execution** for unpacked dev extensions — a blocker hit during an
early spike).

## Artifacts (generated locally; all gitignored)
| File | Purpose | Handling |
|---|---|---|
| `wingman-extension.zip` | source bundle for **Chrome Web Store** upload | regenerate from `extension/` |
| `extension.crx` | signed package for **self-host / enterprise** install | re-pack to update |
| `extension.pem` | signing key for the self-host `.crx` | **SECRET — never commit; back up.** Losing it breaks self-host updates |
| stable self-host ID | derived from `extension.pem` at first pack | used by the enterprise policy below |

> Note: the Web Store assigns its **own** extension ID on upload (different from the self-host ID).
> Use the self-host ID only for the enterprise-policy path.

## Recommended: unlisted Chrome Web Store
Cleanest distribution channel — auto-updates, no admin, no hosting, shareable by link.
1. One-time: a Google account + Chrome Web Store **developer registration ($5 one-time fee)** — `https://chrome.google.com/webstore/devconsole`.
2. Upload `wingman-extension.zip`.
3. Fill the **listing form** (this is where uploads commonly stall):
   - Host-access justification: "Reads the active spreadsheet cell from the Workiva page DOM to assist the operator."
   - Single purpose: "Assist operators editing Workiva spreadsheets."
   - Data use: **collects nothing, sends nothing off-device** — no remote code. Note the manifest
     does declare `alarms`, `storage`, and `debugger` permissions (the `debugger` permission drives
     the local CDP screenshot crops and draws extra review scrutiny), plus host permissions for the
     localhost service — justify each in the form.
4. Set visibility to **Unlisted** (only people with the link can install).
5. Submit for review (~1-3 business days for a content-script extension).
6. Install from the unlisted link on each machine that needs it.
7. **Verify it works** (see below) — do not assume "Added" == working.

## Manifest limits (gotchas that block upload)
- `description` ≤ **132 chars** (a 144-char description was once rejected with "description field too long"; current is 118).
- `name` ≤ 45 chars.
- Icons 16/48/128 required for the listing.
Re-check these before every re-pack.

## Verify it works (post-install)
1. Open a Workiva spreadsheet and click any cell.
2. A small dark pill appears bottom-right: **"Wingman · &lt;cell&gt; ✓"**, updating as you click cells.
   - No pill → extension not loaded, or not on a `*.wdesk.com` tab.
   - Pill reads **"Wingman · can't read cell"** (amber border) → Workiva changed the name-box class; the cell-ID selector drifted (the drift guard in `content.js` caught it). Re-confirm the `.dt-formula-cell-indicator` selector.

## Alternative: macOS enterprise force-install (managed fleets / no Web Store)
Runs the extension by policy; bypasses the dev-load hardening. Needs **admin access** + a hosted update manifest + the `.crx`.
1. Host `extension.crx` + an `update.xml` (Omaha update manifest) at an HTTPS URL you control.
2. Apply a Chrome managed policy (configuration profile or `/Library/Managed Preferences/com.google.Chrome.plist`).
```json
{
  "ExtensionSettings": {
    "<your-self-host-extension-id>": {
      "installation_mode": "force_installed",
      "update_url": "https://YOUR-HOST/wingman/update.xml"
    }
  }
}
```
3. Relaunch Chrome → force-installed → content script runs fully.

## Re-pack after code changes
```
cd wingman
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --pack-extension="$PWD/extension" --pack-extension-key="$PWD/extension.pem" --no-message-box
# then re-zip for the Web Store:
( cd extension && zip -rq ../wingman-extension.zip . -x "*.pem" -x "*.crx" )
```

## Why the dev-load route failed (for the record)
Chrome 149 loads unpacked dev extensions (via `--load-extension` or CDP `Extensions.loadUnpacked`)
but does not execute their content scripts — a hardening against automation/malware. It does **not**
affect Web-Store or policy-force-installed extensions. The content-script DOM read itself is
documented-safe (isolated world, unaffected by page CSP) and was corroborated live (the
`.dt-formula-cell-indicator` address is in the main document; DevTools read ~100% in ~2-3ms).
The only un-ticked box is loading a dev build — which both channels above resolve.
