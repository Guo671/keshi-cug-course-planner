"""Isolated real WebView2 download check; substitutes only the native Save As choice."""
from __future__ import annotations

import argparse
import base64
import tempfile
import threading
import time
from pathlib import Path

import webview


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--disabled', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    payload = (root / 'release/keshi-course-template.xlsx').read_bytes()
    source = (root / 'frontend/course-editor.js').read_text(encoding='utf-8')
    handler = source[source.index('  $("#download-course-template").addEventListener'):source.index('  $("#open-manual-editor")')]
    html = '''<button id="download-course-template">Download</button><script>
    const state={token:'isolated-test'}; const $=s=>document.querySelector(s);
    const toast=(s)=>{document.title=s};
    const fetch=async()=>({ok:true,blob:async()=>new Blob([Uint8Array.from(atob(''' + repr(base64.b64encode(payload).decode()) + '''),c=>c.charCodeAt(0))],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})});
    ''' + handler + '</script>'
    with tempfile.TemporaryDirectory(prefix='keshi-download-check-', ignore_cleanup_errors=True) as tmp:
        output = Path(tmp) / 'saved.xlsx'
        webview.settings['ALLOW_DOWNLOADS'] = not args.disabled
        window = webview.create_window('Keshi isolated download test', html=html, hidden=True)
        results = []

        def exercise():
            from webview.platforms import edgechromium
            native = edgechromium.WinForms

            class SaveDialog:
                FileName = ''
                def ShowDialog(self, _form):
                    self.FileName = str(output)
                    return native.DialogResult.OK

            class FormsProxy:
                SaveFileDialog = SaveDialog
                def __getattr__(self, name):
                    return getattr(native, name)

            edgechromium.WinForms = FormsProxy()
            try:
                window.evaluate_js("document.querySelector('#download-course-template').click()")
                deadline = time.monotonic() + (4 if args.disabled else 20)
                while time.monotonic() < deadline:
                    if output.exists() and output.read_bytes() == payload:
                        results.append(True)
                        break
                    time.sleep(.1)
            finally:
                edgechromium.WinForms = native
                window.destroy()

        window.events.loaded += lambda: threading.Thread(target=exercise, daemon=True).start()
        webview.start(gui='edgechromium', private_mode=False, storage_path=str(Path(tmp)/'browser'))
        assert bool(results) is (not args.disabled), results
        print('PASS: downloads disabled reproduces silent failure' if args.disabled else 'PASS: real WebView2 saved exact XLSX bytes')


if __name__ == '__main__':
    main()
