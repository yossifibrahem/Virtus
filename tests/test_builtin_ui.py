"""Exercise actual browser rendering functions using Node's ES module loader."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_tool_html_escapes_output_and_restricts_media_urls():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    script = r'''
import assert from 'node:assert/strict';
import {renderToolResultHtml} from './static/js/mcp_tool_ui.js';
const url='/api/conversations/00000000-0000-0000-0000-000000000000/tool-media/'+'a'.repeat(64)+'.jpeg';
const html=renderToolResultHtml({text:'<script>alert(1)</script>',media:[url,'javascript:alert(1)','https://evil.example/image']}, 'read');
assert(html.includes('&lt;script&gt;'));
assert(!html.includes('<script>'));
assert(html.includes('src="'+url+'"'));
assert(!html.includes('evil.example'));
assert(!html.includes('javascript:'));
assert(renderToolResultHtml('hello <b>world</b>', 'bash').includes('&lt;b&gt;'));
'''
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
