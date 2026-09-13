"""Browser regression for the credential-free demo; requires agent-browser + Chromium.

Run: python -m pytest scripts/test_demo_browser.py -q
The HTTP fixture starts and stops its own disposable demo process. No live service needed.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from test_demo import demo_url  # noqa: F401,E402 — shared disposable HTTP fixture


def test_browser_workflow(demo_url):
    executable = shutil.which("agent-browser")
    assert executable, "Install agent-browser and run `agent-browser install` first"
    session = "wm-" + uuid.uuid4().hex[:8]

    def run(*args):
        result = subprocess.run([executable, "--session", session, *args],
                                capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    root = "document.getElementById('__wk_wingman__').shadowRoot"
    ready = "!document.getElementById('reset').disabled"
    group = f"[...{root}.querySelectorAll('.wm-grp')].find(g=>g.querySelector('.wm-kind').textContent==='label hygiene')"

    def check(expression):
        run("eval", "(() => { if (!(" + expression + ")) throw new Error('Browser assertion failed'); return true; })()")

    def preview_label():
        run("eval", f"{group}.querySelector('.wm-row').click()")
        run("eval", f"{group}.querySelector('.wm-btn').click()")
        run("wait", "--fn", ready + " && document.getElementById('outcome').textContent.includes('dry-run')")
        check("document.querySelectorAll('.trace-row').length === 0")
        check("document.querySelector('#grid [data-addr=\"A4\"] button').textContent === 'Intergovernmental  revenue '")
        run("set", "viewport", "390", "844", "2")
        check(f"[...{root}.querySelectorAll('.wm-fix')].every(f=>f.scrollWidth<=f.clientWidth+1)")
        run("set", "viewport", "1280", "900", "2")

    def apply_label():
        run("eval", f"[...{group}.querySelectorAll('button')].find(b=>b.textContent==='Apply').click()")

    try:
        run("open", demo_url)
        run("set", "viewport", "1280", "900", "2")
        run("wait", "--fn", ready + f" && {root}.querySelectorAll('.wm-grp').length === 6")
        check(f"{root}.querySelector('.wm-fixall').textContent === 'Fix all 3 safe'")
        check(f"{root}.querySelector('[data-tab=checks]').disabled")
        run("set", "viewport", "390", "844", "2")
        check("document.documentElement.scrollWidth <= innerWidth")
        check(f"[...{root}.querySelectorAll('.wm-row')].every(r=>r.querySelector('.wm-sig').getBoundingClientRect().top >= r.querySelector('.wm-kind').getBoundingClientRect().bottom - 1)")
        check(f"[...{root}.querySelectorAll('.wm-sev')].every(s=>s.scrollWidth <= s.clientWidth + 1)")
        run("set", "viewport", "1280", "900", "2")
        preview_label()
        apply_label()
        run("wait", "--fn", ready + " && document.getElementById('outcome').textContent.includes('applied')")
        check("document.querySelector('#grid [data-addr=\"A4\"] button').textContent === 'Intergovernmental revenue'")
        check("document.querySelectorAll('.trace-row').length === 1")

        run("click", "#reset")
        run("wait", "--fn", ready + f" && {root}.querySelectorAll('.wm-grp').length === 6")
        run("click", "#failure")
        run("wait", "--fn", ready + " && document.getElementById('failure').getAttribute('aria-pressed') === 'true'")
        preview_label()
        apply_label()
        run("wait", "--fn", ready + " && document.getElementById('outcome').textContent.includes('mismatch-reverted')")
        check("document.querySelector('#grid [data-addr=\"A4\"] button').textContent === 'Intergovernmental  revenue '")
        check("document.querySelectorAll('.trace-row').length === 2")

        run("find", "role", "button", "click", "--name", "Review notes", "--exact")
        run("wait", "--fn", ready + f" && {root}.querySelector('.wm-body').textContent.includes('No issues found')")
        check("document.querySelector('#grid [data-addr=\"B2\"] button').textContent === '2025'")
        run("click", "#report")
        run("wait", "--fn", ready + " && document.getElementById('demo-status').textContent.includes('Downloaded')")

        run("set", "viewport", "390", "844", "2")
        check("document.documentElement.scrollWidth <= innerWidth")
        # Count only visible controls; collapsed findings intentionally hide their buttons.
        check(f"[...document.querySelectorAll('button'), ...{root}.querySelectorAll('button')].filter(b=>b.getBoundingClientRect().height>0).every(b=>b.getBoundingClientRect().height>=40)")
        check("devicePixelRatio === 2")
        # Portal embedding opts in explicitly. Normal extension frames still exit.
        run("eval", "(() => {var f=document.createElement('iframe'); f.id='demo-frame'; f.src='/'; document.body.appendChild(f);})()")
        frame = "document.getElementById('demo-frame').contentDocument"
        run("wait", "--fn", f"{frame}?.getElementById('__wk_wingman__')?.shadowRoot.querySelectorAll('.wm-grp').length === 6")
        check(f"{frame}.documentElement.getAttribute('data-copilot-loaded') === 'iframe'")
        run("eval", "document.getElementById('demo-frame').remove()")
        run("eval", "(() => {var f=document.createElement('iframe'); f.id='plain-frame'; f.srcdoc='<script src=\"/wingman-core.js\"></script><script src=\"/wingman-panel.js\"></script>'; document.body.appendChild(f);})()")
        frame = "document.getElementById('plain-frame').contentDocument"
        run("wait", "--fn", f"{frame}?.documentElement.getAttribute('data-copilot-loaded') === 'iframe'")
        check(f"!{frame}.getElementById('__wk_wingman__')")
        run("eval", "document.getElementById('plain-frame').remove()")
        assert not run("errors").strip(), "Browser reported uncaught JavaScript errors"
    finally:
        run("close")
