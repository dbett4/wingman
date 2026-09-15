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

    def scan():
        run("eval", f"{root}.querySelector('[data-tab=scan]').click()")
        run("eval", f"{root}.querySelector('.wm-tab-actions .wm-btn.primary').click()")

    try:
        run("open", demo_url)
        run("set", "viewport", "1280", "900", "2")
        run("wait", "--fn", ready + f" && {root}.querySelector('.wi-address')?.textContent === 'B2'")
        check("document.getElementById('request-count').textContent === '0 simulated API requests'")
        scan()
        run("wait", "--fn", ready + f" && {root}.querySelectorAll('.wm-grp').length === 7")
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
        run("wait", "--fn", ready + f" && !!{root}.querySelector('.wi-inspector')")
        scan()
        run("wait", "--fn", ready + f" && {root}.querySelectorAll('.wm-grp').length === 7")
        run("click", "#failure")
        run("wait", "--fn", ready + " && document.getElementById('failure').getAttribute('aria-pressed') === 'true'")
        preview_label()
        apply_label()
        run("wait", "--fn", ready + " && document.getElementById('outcome').textContent.includes('mismatch-reverted')")
        check("document.querySelector('#grid [data-addr=\"A4\"] button').textContent === 'Intergovernmental  revenue '")
        check("document.querySelectorAll('.trace-row').length === 2")

        run("find", "role", "button", "click", "--name", "Review notes", "--exact")
        run("wait", "--fn", f"{root}.querySelector('.wm-body').textContent.includes('Scan the open sheet')")
        scan()
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
        run("wait", "--fn", f"!!{frame}?.getElementById('__wk_wingman__')?.shadowRoot.querySelector('.wi-inspector')")
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


def test_inspector_evidence_and_navigation(demo_url):
    executable = shutil.which("agent-browser")
    assert executable, "Install agent-browser and run `agent-browser install` first"
    session = "wm-inspect-" + uuid.uuid4().hex[:8]

    def run(*args):
        result = subprocess.run([executable, "--session", session, *args],
                                capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    root = "document.getElementById('__wk_wingman__').shadowRoot"
    ready = "!document.getElementById('reset').disabled"

    def check(expression):
        run("eval", "(() => { if (!(" + expression + ")) throw new Error(" + repr(expression) + "); return true; })()")

    def click(selector):
        run("eval", f"{root}.querySelector({selector!r}).click()")

    try:
        run("open", demo_url)
        run("set", "viewport", "1280", "900", "2")
        run("wait", "--fn", f"!!{root}.querySelector('.wi-inspect') && {ready}")
        check("document.getElementById('request-count').textContent === '0 simulated API requests'")
        for addr, text in [("B7", "=SUM(B3:B6)+12500"), ("C8", "0"), ("B8", "=#REF!"), ("B1", "(blank)")]:
            run("click", f'#grid [data-addr="{addr}"] button')
            run("wait", "--fn", f"{root}.querySelector('.wi-address').textContent === '{addr}'")
            check(f"!{root}.querySelector('.wi-evidence')")
            click(".wi-inspect")
            run("wait", "--fn", ready + f" && !!{root}.querySelector('.wi-evidence')")
            check(f"{root}.querySelector('.wi-evidence dd').textContent === {text!r}")
            check(f"!{root}.querySelector('.wi-sources').open")
            click(".wi-sources > summary")
            check(f"{root}.querySelector('.wi-sources').open")
            if addr == "B7":
                check(f"{root}.querySelector('.wi-sources .wi-evidence').textContent === 'Addresses in formulaB3:B6Literal numbers12500'")
                check(f"{root}.querySelector('.wi-link summary').textContent === 'Source range B3:C7 includes this cell'")
                check(f"!{root}.querySelector('.wi-values')")
                run("eval", f"{root}.querySelector('.wi-read-sources').focus()")
                run("press", "Enter")
                run("wait", "--fn", ready + f" && !!{root}.querySelector('.wi-values')")
                check(f"{root}.activeElement.classList.contains('wi-read-sources')")
                check(f"{root}.querySelector('.wi-sources').open")
                check(f"Array.from({root}.querySelectorAll('.wi-values tbody tr')).map(e => e.textContent).join('|') === 'B32450000|B4875000|B5315000|B694000'")
                check(f"{root}.querySelector('.wi-source-values').textContent.includes('demo-current-0')")
            elif addr == "B8":
                check(f"{root}.querySelector('.wi-sources').textContent.includes('Not resolved#REF!')")
            else:
                check(f"{root}.querySelector('.wi-sources').textContent.includes('No range link covers this cell')")
        check("document.querySelectorAll('.trace-row').length === 0")
        check("document.getElementById('request-count').textContent === '32 simulated API requests'")
        run("find", "role", "button", "click", "--name", "Review notes", "--exact")
        run("wait", "--fn", f"{root}.querySelector('.wi-address').textContent === 'B2'")
        click(".wi-inspect")
        run("wait", "--fn", ready + f" && !!{root}.querySelector('.wi-link')")
        check(f"!{root}.querySelector('.wi-sources').open && {root}.querySelector('.wi-source-summary').textContent.includes('Cell link → D6')")
        click(".wi-sources > summary")
        check(f"{root}.querySelector('.wi-link summary').textContent === 'Linked from C5:D8'")
        run("eval", f"{root}.querySelector('.wi-link summary').focus()")
        run("press", "Enter")
        check(f"{root}.querySelector('.wi-link').open && {root}.querySelector('.wi-link').textContent.includes('demo-published-3')")
        check(f"{root}.querySelector('.wi-sources').textContent.includes('No stored formula')")
        click(".wi-read-sources")
        run("wait", "--fn", ready + f" && !!{root}.querySelector('.wi-values')")
        check(f"{root}.querySelector('.wi-evidence dd').textContent === '2025'")
        check(f"{root}.querySelectorAll('.wi-source-values').length === 2")
        check(f"{root}.querySelector('.wi-source-values').open && {root}.querySelector('.wi-source-values').textContent.includes('Source anchor revision: demo-published-3')")
        check(f"{root}.querySelector('.wi-selected-content').textContent === 'Selected B2 stored content: 2025'")
        check(f"{root}.querySelector('[aria-label=\"Source cells in D6\"] tbody').textContent === 'D62024'")
        run("eval", f"{root}.querySelectorAll('.wi-source-values')[1].querySelector('summary').click()")
        check(f"{root}.querySelectorAll('.wi-source-values')[1].open && {root}.querySelectorAll('.wi-source-values')[1].textContent.includes('Published revision: demo-published-3')")
        check(f"{root}.querySelectorAll('[aria-label=\"Source cells in C5:D8\"] tbody tr').length === 8")
        check(f"{root}.querySelector('[aria-label=\"Source cells in C5:D8\"] tbody tr:nth-child(4)').textContent === 'D62024'")

        # Nonblank destination content and covering range metadata cannot prove connection.
        run("click", '#grid [data-addr="B3"] button')
        run("wait", "--fn", f"{root}.querySelector('.wi-address').textContent === 'B3'")
        click(".wi-inspect")
        run("wait", "--fn", ready + f" && !!{root}.querySelector('.wi-cell-link')")
        check(f"{root}.querySelector('.wi-evidence dd').textContent === 'Accrual'")
        check(f"!{root}.querySelector('.wi-sources').open && {root}.querySelector('.wi-source-summary').textContent.includes('Cell link disconnected')")
        click(".wi-sources > summary")
        check(f"{root}.querySelector('.wi-cell-link').open && {root}.querySelector('.wi-cell-link').textContent.includes('retain its last published value')")
        click(".wi-read-sources")
        run("wait", "--fn", ready + f" && !!{root}.querySelector('.wi-source-values')")
        check(f"!{root}.querySelector('.wi-source-values').querySelector('.wi-values') && {root}.querySelector('.wi-source-values').textContent.includes('No source was followed')")
        check(f"!{root}.querySelector('[aria-label=\"Source cells in D6\"]')")
        check("document.querySelectorAll('.trace-row').length === 0")
        run("find", "role", "button", "click", "--name", "Statement of activities", "--exact")
        run("click", '#grid [data-addr="B1"] button')
        run("wait", "--fn", f"{root}.querySelector('.wi-address').textContent === 'B1'")
        check(f"{root}.querySelector('.wi-scope').textContent.includes('Not connected')")
        run("eval", f"{root}.querySelector('[data-tab=inspect]').focus()")
        for key, tab in [("ArrowRight", "scan"), ("ArrowRight", "workbook"), ("Home", "inspect")]:
            run("press", key)
            check(f"{root}.activeElement.getAttribute('data-tab') === '{tab}'")
            check(f"{root}.querySelector('[role=tabpanel]').getAttribute('aria-labelledby') === 'wm-tab-{tab}'")

        # Hold actual messages to drive otherwise hard-to-reproduce response orderings.
        # These cases verify the controller, not the HTTP client or Workiva integration.
        run("eval", """(() => {
          window.savedSend = chrome.runtime.sendMessage;
          window.pending = [];
          chrome.runtime.sendMessage = (msg, cb) => {
            if (msg.path?.startsWith('/api/inspect') || msg.path?.startsWith('/api/queue')) pending.push({msg, cb});
            else savedSend(msg, cb);
          };
          window.answer = (entry, value, source) => {
            var q = new URL(entry.msg.path, location.origin).searchParams;
            entry.cb({ok:true, data:{target:Object.fromEntries(q), readOnly:true, status:'observed',
              sourceValuesRequested:q.get('sources') === 'true',
              observedAt:new Date().toISOString(), sheetName:'Synthetic reply',
              content:{status:'observed', kind:'number', value}, calculated:{status:'observed', value},
              nativeFormat:{status:'observed', value:{valueFormatType:'NUMBER'}}, source, warnings:[]}});
          };
        })()""")
        click(".wi-inspect")
        run("wait", "--fn", f"{root}.querySelector('.wi-inspect').disabled && pending.length === 1")
        # Change and return to the same cell; identity alone cannot detect the stale request.
        run("click", '#grid [data-addr="C8"] button')
        run("wait", "--fn", f"{root}.querySelector('.wi-address').textContent === 'C8'")
        run("click", '#grid [data-addr="B1"] button')
        run("wait", "--fn", f"{root}.querySelector('.wi-address').textContent === 'B1'")
        click(".wi-inspect")
        run("eval", "answer(pending.pop(), 123); answer(pending.shift(), 999)")
        run("wait", "--fn", f"{root}.querySelector('.wi-evidence dd')?.textContent === '123'")

        # A wider source request is still bound to this selection, and requires a scope echo.
        click(".wi-inspect")
        run("eval", "answer(pending.shift(), 123, {formula:{status:'text_only',references:['C11'],literalNumbers:[],unresolved:[]}})")
        click(".wi-sources > summary")
        click(".wi-read-sources")
        check("pending[0].msg.path.endsWith('&sources=true')")
        run("click", '#grid [data-addr="C8"] button')
        run("wait", "--fn", f"{root}.querySelector('.wi-address').textContent === 'C8'")
        run("eval", "answer(pending.shift(), 999)")
        check(f"!{root}.querySelector('.wi-evidence') && !{root}.querySelector('.wi-values')")
        run("click", '#grid [data-addr="B1"] button')
        run("wait", "--fn", f"{root}.querySelector('.wi-address').textContent === 'B1'")
        click(".wi-inspect")
        run("eval", "answer(pending.shift(), 123, {formula:{status:'text_only',references:['C11'],literalNumbers:[],unresolved:[]}})")
        click(".wi-sources > summary")
        click(".wi-read-sources")
        run("eval", "var entry=pending.shift(); entry.msg.path=entry.msg.path.replace('&sources=true',''); answer(entry,999)")
        check(f"{root}.querySelector('.wi-inspector').textContent.includes('did not match') && !{root}.querySelector('.wi-values')")

        # Source formulas/results and failures have their own visible, escaped evidence.
        click(".wi-inspect")
        run("eval", "window.sourceReply={formula:{status:'text_only',references:['C11'],literalNumbers:[],unresolved:[]},rangeLinks:{status:'observed',items:[]}}; answer(pending.shift(),123,sourceReply)")
        click(".wi-sources > summary")
        click(".wi-read-sources")
        run("eval", "sourceReply.values={status:'observed',groups:[{status:'observed',reference:'C11',range:'C11',tableId:'<img src=x>',revision:'revision-9',basis:'selected_revision',cells:[{addr:'C11',content:{status:'observed',kind:'formula',formula:'=SUM(C1:C10)'},calculated:{status:'observed',value:'-12340'}}]}]}; answer(pending.shift(),123,sourceReply)")
        check(f"{root}.querySelector('.wi-values td').textContent === '=SUM(C1:C10)Formula result: -12340'")
        check(f"!{root}.querySelector('.wi-source-values img')")
        click(".wi-read-sources")
        run("eval", "sourceReply.values.status='partial'; sourceReply.values.groups[0]={status:'unavailable',reference:'C11',tableId:'table',revision:'revision-9',basis:'selected_revision',reason:'Source unavailable; no latest-revision substitute.'}; answer(pending.shift(),123,sourceReply)")
        check(f"!{root}.querySelector('.wi-values') && {root}.querySelector('.wi-source-values').textContent.includes('no latest-revision substitute')")
        click(".wi-sources > summary")
        check(f"{root}.querySelector('.wi-source-summary').textContent.includes('source reads incomplete')")

        # URL-only navigation with unchanged A1 must clear evidence without another read.
        run("eval", "history.replaceState(null, '', '#/spreadsheet/de00/sheet/de02')")
        run("wait", "--fn", f"!{root}.querySelector('.wi-evidence') && {root}.querySelector('.wi-sheet').textContent === 'Sheet de02'")
        check("pending.length === 0")
        run("eval", "history.replaceState(null, '', '#/spreadsheet/abcd/sheet/de02')")
        click(".wi-inspect")
        run("eval", "pending.shift().cb({ok:true, data:{readOnly:true,target:{spreadsheetId:'de00',sheetId:'de02',addr:'B1'}}})")
        run("wait", "--fn", f"{root}.querySelector('.wi-inspector').textContent.includes('did not match')")
        check(f"!{root}.querySelector('.wi-evidence')")

        # A late error or success from an old scan must never replace Inspect.
        for tab in ("scan", "workbook"):
            for error in [True, False]:
                click(f"[data-tab={tab}]")
                click(".wm-tab-actions .wm-btn.primary")
                click("[data-tab=inspect]")
                reply = "{ok:false,offline:true}" if error else "{ok:true,data:{items:[],sheets:[]}}"
                run("eval", f"pending.shift().cb({reply})")
                check(f"!!{root}.querySelector('.wi-inspector')")

        click(".wi-inspect")
        click("[data-tab=scan]")
        run("eval", "answer(pending.shift(), 999)")
        click("[data-tab=inspect]")
        check(f"!{root}.querySelector('.wi-evidence') && !{root}.querySelector('.wi-inspect').disabled")
        click(".wi-inspect")
        run("click", "#open-panel")
        run("eval", "answer(pending.shift(), 999)")
        run("click", "#open-panel")
        check(f"!{root}.querySelector('.wi-evidence')")
        click(".wi-inspect")
        run("eval", "pending.shift().cb({ok:false,offline:true})")
        run("wait", "--fn", f"{root}.querySelector('.wi-inspector').textContent.includes('No cell was inspected')")
        click(".wi-inspect")
        run("eval", "answer(pending.shift(), '<img src=x onerror=alert(1)>')")
        check(f"!{root}.querySelector('.wi-evidence img')")
        check(f"{root}.querySelector('.wi-evidence dd').textContent === '<img src=x onerror=alert(1)>'")

        # Cell success must not hide link failure, even with the details collapsed.
        for status, label in [("unavailable", "Links unavailable"), ("partial", "Link list incomplete")]:
            click(".wi-inspect")
            run("eval", f"answer(pending.shift(), 42, {{rangeLinks:{{status:{status!r},items:[]}}}})")
            check(f"{root}.querySelector('.wi-source-summary').textContent.includes({label!r})")
            check(f"!{root}.querySelector('.wi-sources').textContent.includes('No range link covers')")
        click(".wi-inspect")
        run("eval", "answer(pending.shift(), 42, {rangeLinks:{status:'observed',items:[{direction:'destination',id:'<img src=x>',source:{table:'<script>',rangeLink:'source',revision:'old-revision'},resolution:'unavailable'}]}})")
        check(f"{root}.querySelector('.wi-source-summary').textContent.includes('source unresolved')")
        click(".wi-sources > summary")
        click(".wi-link summary")
        check(f"{root}.querySelector('.wi-link').textContent.includes('old-revision')")
        check(f"!{root}.querySelector('.wi-sources img, .wi-sources script')")

        # Denied metadata is not disconnection; unsupported sources are not guessed.
        for link, label in [
            ("{status:'unavailable',resolution:'unavailable',reason:'Cell link unavailable at its recorded revision.'}", "Cell link unavailable"),
            ("{status:'observed',linkState:'connected',resolution:'unsupported',id:'<img src=x>',source:{type:'richText',anchor:'<script>',revision:'revision-9'},reason:'Non-table source content is not read by this inspector.'}", "Cell-link source unresolved"),
        ]:
            click(".wi-inspect")
            run("eval", f"answer(pending.shift(), 42, {{cellLink:{link},rangeLinks:{{status:'observed',items:[]}}}})")
            check(f"!{root}.querySelector('.wi-sources').open && {root}.querySelector('.wi-source-summary').textContent.includes({label!r})")
            check(f"!{root}.querySelector('.wi-source-summary').textContent.includes('disconnected')")
            click(".wi-sources > summary")
            check(f"{root}.querySelector('.wi-cell-link').open && !{root}.querySelector('.wi-read-sources')")
            check(f"!{root}.querySelector('.wi-sources img, .wi-sources script')")

        run("eval", "document.getElementById('address').textContent = 'B1:C8'")
        run("wait", "--fn", f"{root}.querySelector('.wi-inspector').textContent.includes('not a range')")
        check(f"!{root}.querySelector('.wi-inspect') && !{root}.querySelector('.wi-evidence')")
        run("eval", "history.replaceState(null, '', '#/doc/abcd')")
        run("wait", "--fn", f"{root}.querySelector('.wi-inspector').textContent.includes('Document and comment tracing is not connected')")
        check("pending.length === 0")
        run("eval", "history.replaceState(null, '', '#/spreadsheet/de00/sheet/de01'); document.getElementById('address').textContent='B7'")
        run("wait", "--fn", f"!!{root}.querySelector('.wi-inspect')")
        run("eval", f"{root}.querySelector('.wi-inspect').focus()")
        run("press", "Enter")
        check(f"{root}.activeElement.classList.contains('wi-inspector')")
        run("eval", "answer(pending.shift(), 42)")
        check(f"{root}.activeElement.classList.contains('wi-inspect')")
        click(".wi-inspect")
        run("eval", "chrome.runtime.id=undefined")
        run("wait", "--fn", f"{root}.querySelector('.wi-inspector').textContent.includes('Extension reloaded')")
        run("eval", "answer(pending.shift(), 999)")
        check(f"!{root}.querySelector('.wi-evidence') && !{root}.querySelector('.wi-inspect').disabled")
        assert not run("errors").strip(), "Browser reported uncaught JavaScript errors"
    finally:
        run("close")


def test_connection_check_and_recovery(demo_url):
    executable = shutil.which("agent-browser")
    assert executable, "Install agent-browser and Chromium first"
    session = "wm-connect-" + uuid.uuid4().hex[:8]
    root = "document.getElementById('__wk_wingman__').shadowRoot"

    def run(*args):
        result = subprocess.run([executable, "--session", session, *args],
                                capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def check(expression):
        run("eval", "(() => { if (!(" + expression + ")) throw new Error(" + repr(expression) + "); return true; })()")

    def click(selector):
        run("eval", f"{root}.querySelector({selector!r}).click()")

    try:
        run("open", demo_url)
        run("wait", "--fn", f"!!{root}.querySelector('.wc-toggle')")
        run("eval", "window.sent=[]; window.realSend=chrome.runtime.sendMessage; chrome.runtime.sendMessage=(msg,cb)=>{sent.push(msg.path); realSend(msg,cb)}")
        click(".wc-toggle")
        check(f"{root}.querySelector('.wc-status h3').textContent === 'Not checked' && sent.length === 0")
        check(f"{root}.querySelector('.wm-body').getAttribute('role') === 'region' && {root}.activeElement.classList.contains('wc-page')")
        run("eval", f"{root}.querySelector('.wc-check').focus()")
        run("press", "Enter")
        run("wait", "--fn", f"{root}.querySelector('.wc-status h3').textContent === 'Demo connection' && !document.getElementById('reset').disabled")
        check("JSON.stringify(sent) === JSON.stringify(['/api/connection'])")
        check(f"{root}.querySelector('.wc-facts').textContent.includes('Simulated, not tested') && {root}.activeElement.classList.contains('wc-check')")
        check("document.getElementById('request-count').textContent === '0 simulated API requests' && document.querySelectorAll('.trace-row').length === 0")
        run("eval", "Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:text=>{window.copied=text;return Promise.resolve()}}})")
        click(".wc-copy")
        check("copied.includes('Checked at: 20') && copied.includes('Workbook access: Not tested') && !copied.includes('de00')")
        click(".wc-toggle")
        check(f"!!{root}.querySelector('.wi-inspector') && {root}.querySelector('.wm-body').getAttribute('role') === 'tabpanel'")

        # Controlled responses test the controller, not Workiva or the Chrome broker.
        run("eval", "window.pending=[]; chrome.runtime.sendMessage=(msg,cb)=>{if(msg.path?.startsWith('/api/connection')||msg.path?.startsWith('/api/inspect')||msg.path?.startsWith('/api/queue'))pending.push({msg,cb});else realSend(msg,cb)}")
        click(".wi-inspect")
        click(".wc-toggle")
        run("eval", "pending.shift().cb({ok:false,offline:true})")
        check(f"!!{root}.querySelector('.wc-page') && !{root}.querySelector('.wi-inspector')")
        click(".wc-check")
        check(f"{root}.querySelector('.wc-check').textContent === 'Stop waiting'")
        click(".wc-check")
        run("eval", "pending.shift().cb({ok:true,data:{simulation:true,service:'wingman-demo'}})")
        check(f"{root}.querySelector('.wc-status h3').textContent === 'Check stopped'")

        click(".wc-check")
        click(".wc-toggle")
        click(".wc-toggle")
        run("eval", "pending.shift().cb({ok:true,data:{simulation:true,service:'wingman-demo'}})")
        check(f"{root}.querySelector('.wc-status h3').textContent === 'Not checked'")
        for reply, title in [
            ("{ok:false,configError:true,error:'sensitive-marker'}", "Extension setup needed"),
            ("{ok:false,offline:true}", "Service unreachable"),
            ("{ok:false,status:403,data:{error:'sensitive-marker'}}", "Service access rejected"),
            ("{ok:false,status:500,data:{error:'<img src=x>'}}", "Connection check failed"),
            ("{ok:true,data:null}", "Service update needed"),
            ("{ok:true,data:{service:'wingman',protocol:1,readOnly:true,authorization:'accepted',workivaCredentials:'missing',workivaAccess:'not_tested'}}", "Workiva setup incomplete"),
            ("{ok:true,data:{service:'wingman',protocol:1,readOnly:true,authorization:'accepted',workivaCredentials:'present',workivaAccess:'not_tested'}}", "Service connected"),
        ]:
            click(".wc-check")
            run("eval", f"pending.shift().cb({reply})")
            check(f"{root}.querySelector('.wc-status h3').textContent === {title!r}")
            check(f"{root}.querySelector('.wc-facts').textContent.includes('Workbook accessNot tested') && !{root}.querySelector('.wc-page').textContent.includes('sensitive-marker') && !{root}.querySelector('.wc-page img')")

        # The missing-callback deadline is real; late success cannot undo a timeout.
        click(".wc-check")
        run("wait", "--fn", f"{root}.querySelector('.wc-status h3').textContent === 'Connection timed out'")
        run("eval", "pending.shift().cb({ok:true,data:{simulation:true,service:'wingman-demo'}})")
        check(f"{root}.querySelector('.wc-status h3').textContent === 'Connection timed out'")
        click(".wc-check")
        run("click", "#open-panel")
        run("eval", "pending.shift().cb({ok:true,data:{simulation:true,service:'wingman-demo'}})")
        run("click", "#open-panel")
        click(".wc-toggle")
        check(f"{root}.querySelector('.wc-status h3').textContent === 'Not checked'")
        run("set", "viewport", "390", "844", "2")
        check("document.documentElement.scrollWidth <= innerWidth")
        check(f"[...{root}.querySelectorAll('.wc-toggle,.wc-check,.wc-copy')].every(e=>e.getBoundingClientRect().height>=40)")
        click(".wc-check")
        run("eval", "chrome.runtime.id=undefined")
        run("wait", "--fn", f"{root}.querySelector('.wc-status h3').textContent === 'Extension reloaded'")
        run("eval", "pending.shift().cb({ok:true,data:{simulation:true,service:'wingman-demo'}})")
        check(f"{root}.querySelector('.wc-check').disabled && {root}.querySelector('.wc-status h3').textContent === 'Extension reloaded'")
        assert not run("errors").strip(), "Browser reported uncaught JavaScript errors"
    finally:
        run("close")
