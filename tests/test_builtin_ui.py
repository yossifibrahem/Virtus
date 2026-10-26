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
import {renderToolResultHtml} from './static/js/tool_ui.js';
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


def test_native_adapters_are_separate_and_render_meaningful_states():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    script = r'''
import assert from 'node:assert/strict';
import {getToolDisplayLabel, renderToolResultHtml, getToolKind, getToolIcon, renderToolBody} from './static/js/tool_ui.js';
import {nativeAdapterFor} from './static/js/native_tools/index.js';
import {adapterFor, registerAdapter} from './static/js/tool_adapters/registry.js';
import {workspacePath} from './static/js/native_tools/shared.js';
assert.equal(adapterFor('bash'), null);
assert.equal(adapterFor('read'), null);
assert.equal(getToolKind('read'), 'native');
assert.equal(getToolKind('web_search_exa'), 'mcp');
const icons={toolDefault:'default',custom:'custom-icon'};
assert.equal(getToolIcon('extra',icons,[{name:'extra',server:'server'}],{server:{icon:'custom'}}), 'custom-icon');
assert.equal(getToolIcon('read',icons,[{name:'read',server:'server'}],{server:{icon:'custom'}}), nativeAdapterFor('read').icon);
assert(renderToolBody('extra',{path:'<unsafe>'},'<result>').includes('&lt;unsafe&gt;'));
assert(renderToolBody('extra',{},'<result>').includes('&lt;result&gt;'));
assert(!renderToolBody('extra',{},'ok').includes('native-tool-body'));
assert(adapterFor('web_search_exa'));
registerAdapter({tools:['bash'],renderResult:()=>'<h1>external override</h1>'});
assert(!renderToolResultHtml('hello\n[exit status 0]', 'bash').includes('external override'));
assert.equal(nativeAdapterFor('external_tool'), null);
for (const name of ['bash', 'read', 'write', 'edit']) {
  const adapter = nativeAdapterFor(name);
  assert(!adapter.renderBody({path:'a',command:'echo hi',content:'text',old_text:'a',new_text:'b'}, 'ok').includes('data-native-copy'));
  assert.equal(adapter.copy, undefined);
}
assert(renderToolResultHtml('<unsafe>', 'external_tool').includes('&lt;unsafe&gt;'));
assert.equal(getToolDisplayLabel('read', {path:'src/main.py'}), 'Read · src/main.py');
const bash=nativeAdapterFor('bash');
assert.equal(bash.status('out\n[exit status 1]').tone, 'error');
assert.equal(bash.status('out\n[exit status 0]').label, 'Exit 0');
assert.equal(bash.status('Error: Operation timed out').label, 'Timed out');
assert.equal(bash.status('Error: Operation cancelled').label, 'Cancelled');
assert(bash.renderBody({command:'echo <script>'}, '\n[exit status 0]').includes('(No output)'));
assert(!bash.renderBody({command:'echo <script>'}, '').includes('<script>'));
const read=nativeAdapterFor('read');
const html=read.renderBody({path:'a.py',range:'10:11'}, '10: <img>\n11: second\n[more content]');
assert(html.includes('native-line-number'));
assert(html.includes('&lt;img&gt;'));
assert(html.includes('[more content]'));
assert(html.includes('Range 10:11'));
const write=nativeAdapterFor('write');
assert(write.renderBody({path:'empty',content:''}, 'Wrote empty (0 bytes)').includes('0 lines'));
assert(write.renderBody({path:'text',content:'one\ntwo\n'}, 'ok').includes('2 lines'));
const large='x'.repeat(10000);
assert(write.renderBody({path:'large',content:large}, 'ok').includes('preview shortened'));
const edit=nativeAdapterFor('edit');
const replacement=edit.renderBody({path:'a',old_text:'<old>',new_text:'',replace_all:true}, 'Error: no match');
assert(replacement.includes('Requested replacement'));
assert(replacement.includes('Replace all exact matches'));
assert(replacement.includes('Delete matching text'));
assert(replacement.includes('&lt;old&gt;'));
assert.equal(edit.status('Error: no match').tone, 'error');
assert.equal(workspacePath('/etc/passwd'), null);
assert.equal(workspacePath('../secret'), null);
assert.equal(workspacePath('/workspace/../secret'), null);
assert.equal(workspacePath('src/main.py'), '/workspace/src/main.py');
'''
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    root = Path(__file__).resolve().parents[1]
    for module in (root / 'static/js/native_tools').glob('*.js'):
        assert "from '../mcp" not in module.read_text()
        assert 'tool_adapters/' not in module.read_text()
