"""Browser module regressions using Node, with no network or Docker dependency."""
from pathlib import Path
import shutil
import subprocess

import pytest


def run_node(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    result = subprocess.run(
        [node, '--experimental-vm-modules', '--input-type=module', '-e', script],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_sse_handles_fragmented_unicode_truncation_and_callback_errors():
    run_node(r'''
import assert from 'node:assert/strict';
import {readSSEStream} from './static/js/stream_consumer.js';
const bytes = new TextEncoder().encode(': heartbeat\n\ndata: broken\n\ndata: {"type":"text","content":"🌍"}\n\ndata: [DONE]\n\n');
const body = new ReadableStream({start(controller) {
  for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
  controller.close();
}});
const seen = [];
assert(await readSSEStream({body}, event => {seen.push(event); return true;}));
assert.deepEqual(seen, [{type:'text',content:'🌍'}]);
assert(!body.locked);
const response = text => ({body: new ReadableStream({start(controller) {
  controller.enqueue(new TextEncoder().encode(text)); controller.close();
}})});
await assert.rejects(readSSEStream(response('data: {}\n\n'), () => true), /before completion/);
const failed = response('data: {}\n\ndata: [DONE]\n\n');
await assert.rejects(readSSEStream(failed, () => {throw new Error('Handler failed');}), /Handler failed/);
assert(!failed.body.locked);
''')


def test_conversation_activity_survives_navigation_and_clears_background_turns():
    run_node(r'''
import assert from 'node:assert/strict';
import {api} from './static/js/api.js';
import * as activity from './static/js/conversation_activity.js';
const {activeTurns, updateConversationActivity:update, syncConversationActivity:sync, workingTitleAttributes} = activity;
let writes = 0;
const indicator = ids => ({
  dataset:{workingConversations:ids}, 'aria-busy':'false',
  getAttribute(name){return this[name];},
  setAttribute(name,value){this[name]=value; writes++;},
});
const isWorking = el => el['aria-busy'] === 'true';
const a = indicator('a'), b = indicator('b'), folder = indicator('a b');
let indicators = [a, b, folder];
globalThis.CSS = {escape:s=>s};
globalThis.document = {querySelectorAll:selector => {
  const ids = [...selector.matchAll(/~="([^"]+)"/g)].map(match=>match[1]);
  return indicators.filter(el=>el.dataset.workingConversations.split(' ').some(id=>ids.includes(id)));
}};
const timers = [];
globalThis.setTimeout = callback => {timers.push(callback); return 1;};
const start = id => {activeTurns.set(id, {}); update(id);};
const finish = (id, disconnected=false) => {activeTurns.delete(id); update(id, disconnected);};
const initialVersion = activity.activityVersion;
start('a');
assert(isWorking(a));
assert(!isWorking(b));
assert(isWorking(folder));
assert(workingTitleAttributes('a').includes('aria-busy="true"'));
const rebuiltA = indicator('a');
indicators = [rebuiltA, b, folder];
sync([{id:'a'}], initialVersion);
assert(workingTitleAttributes('a').includes('aria-busy="true"'));
start('b');
const staleVersion = activity.activityVersion;
finish('a');
assert(!isWorking(rebuiltA));
assert(isWorking(b), 'Background completion must not clear another running chat');
assert(isWorking(folder));
sync([{id:'a',active_stream_id:'old-stream'}], staleVersion);
assert(!isWorking(rebuiltA), 'A stale response cannot revive a completed turn');
finish('b');
for (let i=0; i<1000; i++) {start(`chat-${i}`); finish(`chat-${i}`);}
assert.equal(activeTurns.size, 0);
assert.equal(timers.length, 0, 'Local streams never need status polling');
assert(!isWorking(folder));

// Server-only turns poll compact status; attaching a consumer stops requests.
sync([{id:'a',active_stream_id:'server-stream'}]);
assert(isWorking(rebuiltA));
start('a');
sync([{id:'a',active_stream_id:'server-stream'}]);
api.get = async () => {throw new Error('Local consumer should prevent polling');};
await timers.shift()();
assert.equal(timers.length, 0);

// A disconnected consumer hands off to polling instead of reporting completion.
finish('a', true);
assert(isWorking(rebuiltA));
api.get = async url => {
  assert.equal(url, '/api/conversations/activity');
  return [{id:'a',active_stream_id:'server-stream'}];
};
const writesBeforePoll = writes;
await timers.shift()();
assert(isWorking(rebuiltA));
assert.equal(writes, writesBeforePoll, 'Unchanged activity must not rewrite DOM attributes');
api.get = async () => {throw new Error('Temporary network failure');};
await timers.shift()();
assert(isWorking(rebuiltA));
assert.equal(timers.length, 1);
api.get = async () => [];
await timers.shift()();
assert(!isWorking(rebuiltA));
assert(!isWorking(folder));
assert.equal(timers.length, 0);

// Completion while a poll is in flight invalidates that older response.
sync([{id:'a',active_stream_id:'server-stream'}]);
let resolvePoll;
api.get = () => new Promise(resolve=>resolvePoll=resolve);
const polling = timers.shift()();
start('a'); finish('a');
resolvePoll([{id:'a',active_stream_id:'server-stream'}]);
await polling;
assert(!isWorking(rebuiltA));
assert.equal(timers.length, 0);
assert(workingTitleAttributes('a').includes('aria-busy="false"'));
assert.equal(rebuiltA['aria-busy'], 'false');
''')


def test_chat_approvals_survive_navigation_without_blocking_or_cross_chat_cancellation():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const state = {convId:'a',isStreaming:true,streamId:'stream-a'};
const posts=[], approvals=[], results=[], running=[], activityChanges=[], markdown=[];
const frames=new Map(); let nextFrame=0;
const noop = () => {};
const renderer = Object.fromEntries([
  'appendAssistantStatus','finalizeStreamingMessage','setStreamingMessageLogIndex',
  'scrollToBottom','renderAllMessages','refreshMessageFooter','createThinkingBlock',
  'updateThinkingBlock','finalizeThinkingBlock',
].map(name => [name, noop]));
Object.assign(renderer, {
  createStreamingMessage: () => ({}),
  createToolStrip: name => ({name}),
  toolStripSetRunning: strip => running.push(strip),
  toolStripFinalize: strip => results.push(strip),
  toolStripSetApproval: (strip,name,args,decide) => {
    const approval={strip,name,args,decide,disposed:false};
    approvals.push(approval);
    return () => approval.disposed=true;
  },
});
const modules = {
  './api.js': {api:{post:async (url,body) => {posts.push({url,body});}}},
  './state.js': {state},
  './renderer.js': renderer,
  './markdown.js': {applyMarkdown:(el,text)=>markdown.push(text)},
  './conversations.js': {persistConversationFor:noop,createNewConversation:noop},
  './file_panel.js': {refreshFilePanel:async () => {}},
  './format.js': {escapeHtml:value=>value},
  './stream_consumer.js': {readResponseError:noop,readSSEStream:async()=>true},
  './chat_attachments.js': {initImageAttachments:noop,hasPendingAttachments:noop},
  './chat_payloads.js': {buildApiMessages:noop,buildToolsPayload:noop,buildMcpToolMetaPayload:noop},
  './ids.js': {createClientId:noop},
  './chat_send.js': {sendMessage:noop},
  './chat_edit.js': {editAndResend:noop,regenerateFrom:noop},
  './chat_log_utils.js': {lastAssistantMessageIndex:()=>-1},
  './conversation_activity.js': {
    activeTurns:new Map(),
    updateConversationActivity:(id,disconnected=false)=>activityChanges.push([id,disconnected]),
  },
};
const context=vm.createContext({document:{addEventListener:noop,getElementById:()=>null},AbortController,
 requestAnimationFrame:callback=>{frames.set(++nextFrame,callback);return nextFrame;},
 cancelAnimationFrame:id=>frames.delete(id),
});
const source=readFileSync('static/js/chat.js','utf8')+
  '\nexport {createStreamContext,processSSEEvent,reattachRuntime,activeTurns,attachServerStream,releaseTurnUI};';
const module=new vm.SourceTextModule(source,{context});
const linked=new Map();
await module.link(specifier=>{
  if (!linked.has(specifier)) {
    const exports=modules[specifier];
    assert(exports, specifier);
    linked.set(specifier,new vm.SyntheticModule(Object.keys(exports),function(){
      for(const [name,value] of Object.entries(exports)) this.setExport(name,value);
    },{context}));
  }
  return linked.get(specifier);
});
await module.evaluate();
const {createStreamContext,processSSEEvent:apply,reattachRuntime,activeTurns,stopAssistantTurn}=module.namespace;
const turn={convId:'a',messages:[],displayLog:[]};
const ctx=createStreamContext(turn,'stream-a');
await apply({type:'text',content:'First'},ctx);
await apply({type:'text',content:' second'},ctx);
assert.equal(frames.size,1,'Token bursts render Markdown only once per animation frame');
assert.equal(markdown.length,0);
const paint=frames.values().next().value;frames.clear();paint();
assert.deepEqual(markdown,['First second']);
await apply({type:'text',content:' third'},ctx);
assert.equal(frames.size,1);
state.convId='b';
module.namespace.releaseTurnUI(ctx);
assert.equal(frames.size,0,'Navigation cancels queued Markdown work and releases old DOM');
assert.equal(ctx.contentEl,null);
await apply({type:'text',content:' background'},ctx);
assert.equal(frames.size,0,'Background tokens must not build detached DOM');
state.convId='a';
reattachRuntime({turn,ctx});
assert.equal(markdown.at(-1),'First second third background');
await apply({type:'text',content:' final'},ctx);
await apply({type:'tool_start',name:'view'},ctx);
await apply({type:'tool_start',name:'external'},ctx);
await apply({type:'tool_running',name:'view'},ctx);
await apply({type:'tool_result',name:'view',result:'file'},ctx);
assert.equal(frames.size,0,'Tool boundaries cancel stale partial Markdown renders');
await apply({type:'tool_approval_required',name:'external',call_id:'one',args:{}},ctx);
assert.equal(approvals.length,1,'Approval must not block event consumption');
assert.equal(approvals[0].strip,ctx.toolStrips[1],'Mixed auto/manual tools must use the matching strip');
await apply({type:'tool_result',name:'external',result:'Timed out'},ctx);
assert(approvals[0].disposed);
assert.equal(results[1],ctx.toolStrips[1]);

// An approval arriving in a background chat stays pending until it is visible.
state.convId='b';
await apply({type:'tool_start',name:'external'},ctx);
await apply({type:'tool_approval_required',name:'external',call_id:'two',args:{x:1}},ctx);
assert.equal(posts.length,0,'Navigation must not deny a tool call');
state.convId='a';
const runtime={turn,ctx,streamId:'stream-a'};
activeTurns.set('a',runtime);
reattachRuntime(runtime);
assert.equal(approvals.length,2);
assert.equal(approvals[1].name,'external');

let otherDisposed=false;
activeTurns.set('b',{ctx:{disposeApproval:()=>otherDisposed=true}});
await stopAssistantTurn();
assert(approvals[1].disposed);
assert(!otherDisposed,'Stopping one chat must not deny another chat approvals');
assert.equal(posts.at(-1).url,'/api/chat/cancel');
assert.equal(posts.at(-1).body.stream_id,'stream-a');
assert(state.isStreaming,'Composer waits for authoritative completion');
await apply({type:'assistant_done',messages:[],displayLog:[]},ctx);
assert(ctx.assistantDone);

// The real attachment lifecycle releases local history, but distinguishes a
// terminal stream from a disconnect while the independent worker is running.
activeTurns.delete('a');
state.convId='b';
modules['./api.js'].api.stream=async()=>({ok:true});
assert(await module.namespace.attachServerStream('a','success',{title:'Working'}));
assert.deepEqual(activityChanges.slice(-2),[['a',false],['a',false]]);
assert(!activeTurns.has('a'));
modules['./api.js'].api.stream=async()=>{throw new Error('Disconnected');};
assert(!await module.namespace.attachServerStream('a','disconnected',{title:'Working'}));
assert.deepEqual(activityChanges.slice(-2),[['a',false],['a',true]]);
assert(!activeTurns.has('a'),'Disconnected consumers must release their retained history');
assert(activeTurns.has('b'),'Finishing a background consumer must preserve other turns');
''')


def test_shared_source_preserves_lines_escapes_and_bounds_large_previews():
    run_node(r'''
import assert from 'node:assert/strict';
import {renderSource} from './static/js/source_view.js';
import view from './static/js/native_tools/view.js';
const raw = '<script>\r\n\tvalue & "text"\r\n\r\n';
const numbered = '1: <script>\n2: \tvalue & "text"\n3: \n';
assert.equal(renderSource(raw), renderSource(numbered, {numbered: true}));
assert(!renderSource(raw).includes('<script>'));
assert(renderSource(raw).includes('&lt;script&gt;'));
assert.equal((renderSource('').match(/class="source-line"/g) || []).length, 1);
const range = view.renderResult('105: <img>\n106: \n[more content]');
assert(range.includes('>105</span>'));
assert(range.includes('&lt;img&gt;'));
assert(range.includes('[more content]'));
const lines = Array.from({length: 300}, (_, i) => `${i + 1}: line ${i + 1}`).join('\n');
assert(view.renderResult(lines).includes('line 300'), 'View must not silently discard lines after 250');
const large = renderSource('row\n'.repeat(100000));
assert.equal((large.match(/class="source-line"/g) || []).length, 2000);
assert(large.includes('Preview shortened'));
assert(renderSource('x'.repeat(200000)).includes('Preview shortened'));
let highlights = 0;
globalThis.hljs = {getLanguage: () => true, highlight: () => { highlights++; return {value: 'highlighted'}; }};
renderSource('row\n'.repeat(500), {filename:'large.py'});
assert.equal(highlights, 0, 'Large files should skip per-line syntax parsing');
renderSource('print(1)', {filename:'small.py'});
assert.equal(highlights, 1);
''')


def test_file_preview_ignores_stale_responses_and_clears_previous_file_actions():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {escapeHtml, formatBytes, fileExtension} from './static/js/format.js';
function element() {
  const classes = new Set();
  return {textContent:'', innerHTML:'', dataset:{}, children:[], disabled:false,
    listeners:{}, addEventListener(name, listener) { this.listeners[name] = listener; },
    setAttribute(name, value) { this[name] = value; },
    classList:{add: (...names) => names.forEach(n => classes.add(n)),
      remove: (...names) => names.forEach(n => classes.delete(n)),
      contains: n => classes.has(n), toggle: (n, on) => on ? classes.add(n) : classes.delete(n)},
    replaceChildren() { this.children = []; this.innerHTML = ''; },
    appendChild(child) { this.children.push(child); },
    removeAttribute(name) { delete this.dataset[name.replace('data-', '')]; },
    querySelectorAll: () => [], querySelector: () => null, contains: () => false,
  };
}
const els = Object.fromEntries(['panel','toggle','list','title','body','copy','download','toggleRender'].map(k => [k,element()]));
const pending = [];
const state = {convId:'first'};
let disposedPdfs = 0;
const modules = {
  './api.js':{api:{get: url => new Promise((resolve, reject) => pending.push({url,resolve,reject}))}},
  './state.js':{state, STORAGE_KEYS:{}}, './icons.js':{ICONS:{}},
  './markdown.js':{applyMarkdown(){}}, './source_view.js':{renderSource: value => value},
  './pdf_preview.js':{mountPdfPreview(body,url) {
    const preview=element(); preview.className='file-preview-pdf'; preview.url=url;
    body.appendChild(preview); return () => disposedPdfs++;
  }},
  './ui.js':{showToast(){}}, './storage.js':{storage:{}},
  './format.js':{escapeHtml,formatBytes,fileExtension},
};
globalThis.document = {createElement:element};
let code = readFileSync('static/js/file_panel.js', 'utf8');
code += '\nexport function setup(elements) { els = elements; }\nexport {loadFilePreview, closePreview, loadFileList};';
const module = new vm.SourceTextModule(code);
await module.link(specifier => new vm.SyntheticModule(Object.keys(modules[specifier]), function() {
  for (const [name,value] of Object.entries(modules[specifier])) this.setExport(name,value);
}));
await module.evaluate();
const ui = module.namespace;
ui.setup(els);
const file = name => ({name, path:'/workspace/'+name, previewable:true, content:'source for '+name});
const old = ui.loadFilePreview('/workspace/old.py');
const fresh = ui.loadFilePreview('/workspace/new.py');
pending[1].resolve(file('new.py'));
await fresh;
pending[0].resolve(file('old.py'));
await old;
assert.equal(els.title.textContent,'new.py');
assert.equal(els.download.dataset.path,'/workspace/new.py');
const binary = ui.loadFilePreview('/workspace/binary');
assert.equal(els.copy.disabled,true);
assert.equal(els.toggleRender.hidden,true);
assert.equal(els.download.dataset.path,undefined);
pending[2].resolve({name:'binary',path:'/workspace/binary',previewable:false});
await binary;
assert.equal(els.copy.disabled,true);
assert.equal(els.download.disabled,false);
const closing = ui.loadFilePreview('/workspace/late.py');
ui.closePreview();
pending[3].resolve(file('late.py'));
await closing;
assert.equal(els.title.textContent,'Preview');
assert.equal(els.copy.disabled,true);
const navigating = ui.loadFilePreview('/workspace/old-chat.py');
state.convId = 'second';
ui.resetFilePanel();
pending[4].resolve(file('old-chat.py'));
await navigating;
assert.equal(els.title.textContent,'Preview');
const listing = ui.loadFileList();
state.convId = 'third';
ui.resetFilePanel();
pending[5].resolve({error:'stale workspace error'});
await listing;
assert(!els.list.innerHTML.includes('stale workspace'));
const broken = ui.loadFilePreview('/workspace/broken');
pending[6].reject(new Error('Connection lost'));
await broken;
assert(els.body.innerHTML.includes('Connection lost'));
assert.equal(els.download.disabled,true);
const pdf = ui.loadFilePreview('/workspace/report.pdf');
const pdfData = {name:'report.pdf',path:'/workspace/report.pdf',previewable:true,
  preview_kind:'pdf',content:null,modified:10,size:20,version:'pdf-v1'};
pending[7].resolve(pdfData);
await pdf;
const visual = els.body.children[0];
assert.equal(visual.className,'file-preview-pdf');
assert.equal(visual.url,'/api/conversations/third/files/pdf?path=%2Fworkspace%2Freport.pdf');
assert.equal(visual.children.length,0);
assert.equal(els.copy.disabled,true);
assert.equal(els.toggleRender.hidden,true);
els.body.querySelector = () => visual;
const refreshPdf = ui.loadFilePreview('/workspace/report.pdf');
assert(pending[8].url.includes('version=pdf-v1'));
pending[8].resolve({unchanged:true});
await refreshPdf;
assert.equal(els.body.children[0],visual, 'Unchanged PDF must keep its viewer and scroll position');
const changedPdf = ui.loadFilePreview('/workspace/report.pdf');
pending[9].resolve({...pdfData,modified:11});
await changedPdf;
assert.notEqual(els.body.children[0],visual, 'Changed PDF must reload');
assert.equal(disposedPdfs,1,'Changed PDFs release their worker and canvases');
const image = ui.loadFilePreview('/workspace/image.png');
pending[10].resolve({name:'image.png',path:'/workspace/image.png',previewable:true,version:'image-v1',
  preview_kind:'image',content:null,images:[{mime:'image/jpeg',data:'/9j/'}, {mime:'text/html',data:'bad'}]});
await image;
assert.equal(disposedPdfs,2,'Switching files releases the PDF renderer');
assert.equal(els.body.children[0].children.length,1);
assert.equal(els.copy.disabled,true);
assert.equal(els.toggleRender.hidden,true);
const picture = els.body.children[0];
const refreshImage = ui.loadFilePreview('/workspace/image.png');
assert(pending[11].url.includes('version=image-v1'));
pending[11].resolve({unchanged:true});
await refreshImage;
assert.equal(els.body.children[0],picture,'Unchanged images must not be recreated');
const text = ui.loadFilePreview('/workspace/notes.py');
pending[12].resolve({...file('notes.py'),version:'text-v1'});
await text;
const source = els.body.children[0];
const refreshText = ui.loadFilePreview('/workspace/notes.py');
assert(pending[13].url.includes('version=text-v1'));
pending[13].resolve({unchanged:true});
await refreshText;
assert.equal(els.body.children[0],source,'Unchanged text must not be highlighted again');
assert.equal(els.copy.disabled,false);
''')


def test_pdf_preview_renders_visible_pages_and_releases_resources():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
class Element {
  children=[]; style={}; clientWidth=400;
  setAttribute(name,value) { this[name]=value; }
  appendChild(child) { child.parent=this; this.children.push(child); }
  append(...children) { children.forEach(child=>this.appendChild(child)); }
  remove() { this.parent.children=this.parent.children.filter(child=>child!==this); }
  getContext() { return {}; }
}
globalThis.window={document:{createElement:()=>new Element()},devicePixelRatio:3};
let intersection,resize,destroyed=0,renders=0,mode='normal',options;
globalThis.IntersectionObserver=class {
  constructor(callback,config) { this.callback=callback; this.config=config; intersection=this; }
  observe() {} disconnect() { this.disconnected=true; }
};
globalThis.ResizeObserver=class {
  constructor(callback) { this.callback=callback; resize=this; }
  observe() {} disconnect() { this.disconnected=true; }
};
const requested=[];
const pdf={numPages:12, async getPage(number) {
  requested.push(number);
  return {getViewport:({scale})=>({width:600*scale,height:800*scale}),cleanup(){},
    render() { renders++; return {promise:Promise.resolve(),cancel(){}}; }};
}};
const library=new vm.SyntheticModule(['getDocument','GlobalWorkerOptions'],function() {
  this.setExport('GlobalWorkerOptions',{});
  this.setExport('getDocument',config=>{
    options=config;
    return {promise:mode==='password' ? Promise.reject({name:'PasswordException'}) : Promise.resolve(pdf),
      destroy(){destroyed++; return Promise.resolve();}};
  });
});
await library.link(()=>{}); await library.evaluate();
const module=new vm.SourceTextModule(readFileSync('static/js/pdf_preview.js','utf8'),{
  initializeImportMeta(meta) { meta.url='file:///app/static/js/pdf_preview.js'; },
  importModuleDynamically:()=>library,
});
await module.link(()=>{}); await module.evaluate();
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const container=new Element();
const dispose=module.namespace.mountPdfPreview(container,'/example.pdf','Example');
await settle();
const root=container.children[0];
assert.equal(root.children.length,12,'All pages must remain reachable in one scroll');
assert.deepEqual(requested,[1],'Loading must not decode every page');
assert.equal(renders,0);
assert.equal(options.disableAutoFetch,true);
assert.equal(options.disableStream,true);
assert.equal(intersection.config.root,container);
const first=root.children[0],last=root.children[11];
intersection.callback([{target:first,isIntersecting:true}]);
await settle();
const canvas=first.children[0].children[0];
assert(canvas.width<=800,'Pixel density must be capped');
assert(canvas.width*canvas.height<=2000000+2000);
intersection.callback([{target:first,isIntersecting:false},{target:last,isIntersecting:true}]);
await settle();
assert.equal(canvas.width,0,'Offscreen canvases must release their backing storage');
assert.equal(first.children[0].children.length,0);
assert(last.children[0].children[0].width>0);
assert.equal(renders,2);
assert.deepEqual(requested,[1,1,12]);
last.children[0].clientWidth=300;
resize.callback(); await settle();
assert.equal(renders,3,'Panel resizing must update visible pages');
dispose();
assert.equal(last.children[0].children.length,0);
assert.equal(destroyed,1);
assert(intersection.disconnected && resize.disconnected);
mode='password';
const locked=new Element();
const closeLocked=module.namespace.mountPdfPreview(locked,'/locked.pdf','Locked');
await settle();
assert.equal(locked.children[0].children[0].role,'alert');
assert(locked.children[0].children[0].textContent.includes('password'));
closeLocked();
''')


def test_streaming_scroll_is_batched_and_respects_reader_position():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const events = {};
const frames = [];
const messages = {scrollHeight:1000, scrollTop:0, clientHeight:400,
  addEventListener: (type, handler) => events[type] = handler};
const state = {isStreaming:true, autoScrollStreaming:true};
globalThis.document = {addEventListener:(type,handler) => events[type] = handler, getElementById:() => null};
globalThis.requestAnimationFrame = fn => { frames.push(fn); return frames.length; };
const dependencies = {'./dom.js':{$:()=>messages, createElement(){}, remove(){}}, './state.js':{state}};
const module = new vm.SourceTextModule(readFileSync('static/js/renderer_core.js', 'utf8'));
await module.link(specifier => new vm.SyntheticModule(Object.keys(dependencies[specifier]), function() {
  for (const [name,value] of Object.entries(dependencies[specifier])) this.setExport(name,value);
}));
await module.evaluate();
events.DOMContentLoaded();
const {scrollToBottom} = module.namespace;
for(let i=0;i<100;i++) scrollToBottom();
assert.equal(frames.length,1);
frames.shift()();
assert.equal(messages.scrollTop,1000);
messages.scrollTop = 100;
events.scroll({currentTarget:messages});
scrollToBottom();
assert.equal(frames.length,0, 'Reading history must not schedule autoscroll');
scrollToBottom(true);
assert.equal(frames.length,1);
frames.shift()();
assert.equal(messages.scrollTop,1000);
state.autoScrollStreaming = false;
scrollToBottom();
assert.equal(frames.length,0);
''')


def test_tabs_share_keyboard_navigation_and_panel_visibility():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const handlers = {};
const panels = {};
let focused;
const tabs = ['first','second','third'].map((name,index) => ({
  attributes: {'aria-controls':name,'aria-selected':String(index === 0)},
  classList:{toggle(){},contains:()=>false}, getAttribute(key) {return this.attributes[key];},
  setAttribute(key,value) {this.attributes[key]=value;}, focus() {focused=this;},
}));
for (const tab of tabs) panels[tab.attributes['aria-controls']] = {classList:{toggle(){}}};
globalThis.window = {matchMedia: () => ({matches:false})};
globalThis.document = {addEventListener(){}, getElementById:id => panels[id]};
const module = new vm.SourceTextModule(readFileSync('static/js/ui.js','utf8'));
await module.link(specifier => {
  const exports = specifier.includes('storage') ? {storage:{}} : {STORAGE_KEYS:{}};
  return new vm.SyntheticModule(Object.keys(exports),function() {
    for(const [key,value] of Object.entries(exports)) this.setExport(key,value);
  });
});
await module.evaluate();
module.namespace.initTabs({querySelectorAll:() => tabs, setAttribute(){}, addEventListener:(type,fn) => handlers[type]=fn});
assert.doesNotThrow(() => module.namespace.initTabs(null));
assert.doesNotThrow(() => module.namespace.initTabs({querySelectorAll:()=>[]}));
assert.doesNotThrow(() => module.namespace.initTabs({querySelectorAll:()=>[{
  getAttribute:()=>null, dataset:{tab:'missing-panel'},
}]}));
const key = (key,target) => handlers.keydown({key,target,preventDefault(){}});
assert.equal(panels.first.hidden,false);
assert.equal(panels.second.hidden,true);
key('ArrowRight',tabs[0]);
assert.equal(focused,tabs[1]);
assert.equal(tabs[1].tabIndex,0);
assert.equal(tabs[0].tabIndex,-1);
assert.equal(panels.first.hidden,true);
assert.equal(panels.second.hidden,false);
key('End',tabs[1]);
assert.equal(focused,tabs[2]);
key('ArrowRight',tabs[2]);
assert.equal(focused,tabs[0]);
key('ArrowLeft',tabs[0]);
assert.equal(focused,tabs[2]);
handlers.click({target:{closest:() => tabs[1]}});
assert.equal(panels.second.hidden,false);
key('Home',tabs[1]);
assert.equal(focused,tabs[0]);
// Already-open pages can still have the older data-tab markup.
for (const [index,tab] of tabs.entries()) {
  tab.dataset={tab:['first','second','third'][index]};
  delete tab.attributes['aria-controls'];
  delete tab.attributes['aria-selected'];
}
module.namespace.initTabs({querySelectorAll:()=>tabs,setAttribute(){},addEventListener:(type,fn)=>handlers[type]=fn});
assert.equal(tabs[0].getAttribute('role'),'tab');
handlers.click({target:{closest:()=>tabs[2]}});
assert.equal(panels.third.hidden,false);
assert.equal(panels.first.hidden,true);

''')


def test_chat_navigation_keeps_latest_selection_and_new_chat_cancels_pending_open():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const pending=[];
const state={convId:null};
const rendered=[];
const elements=Object.fromEntries(['chat-title-input','chat-folder-prefix','chat-title-separator','conv-list'].map(id => [id,{
  value:'',setAttribute(){},addEventListener(){},classList:{remove(){}},
}]));
globalThis.document={
  addEventListener(){}, dispatchEvent(){}, getElementById:id=>elements[id],
  querySelector:()=>null, querySelectorAll:()=>[],
};
globalThis.CSS={escape:s=>s};
globalThis.CustomEvent=class {};
globalThis.window={innerWidth:1200};
const dependencies={
  './api.js':{api:{get:()=>new Promise(resolve=>pending.push(resolve))}},
  './state.js':{state,STORAGE_KEYS:{}}, './storage.js':{storage:{set(){},remove(){}}},
  './renderer.js':{clearMessages:()=>rendered.push('empty'),renderAllMessages:log=>rendered.push(log[0].content)},
  './ui.js':{toggleSidebar(){},openModal(){},closeModal(){},showToast(){}}, './icons.js':{ICONS:{}},
  './format.js':{escapeHtml:s=>s,formatConversationDate:()=>''}, './file_panel.js':{refreshFilePanel(){},resetFilePanel(){}},
  './conversation_activity.js':{activityVersion:0,workingTitleAttributes:()=>'',syncConversationActivity(){}},
};
const module=new vm.SourceTextModule(readFileSync('static/js/conversations.js','utf8'));
await module.link(specifier=>new vm.SyntheticModule(Object.keys(dependencies[specifier]),function(){
  for(const [key,value] of Object.entries(dependencies[specifier])) this.setExport(key,value);
}));
await module.evaluate();
const {openConversation,startNewChat}=module.namespace;
const first=openConversation('first');
const second=openConversation('second');
pending[1]({title:'Second',displayLog:[{content:'second content'}]});
await second;
pending[0]({title:'First',displayLog:[{content:'first content'}]});
await first;
assert.equal(state.convId,'second');
assert.deepEqual(rendered,['second content']);
const old=openConversation('old');
startNewChat();
pending[2]({title:'Old',displayLog:[{content:'old content'}]});
await old;
assert.equal(state.convId,null);
assert.deepEqual(rendered,['second content','empty']);
let finishCreate;
dependencies['./api.js'].api.post=()=>new Promise(resolve=>finishCreate=resolve);
const creating=module.namespace.createNewConversation();
startNewChat();
finishCreate({id:'created-in-background'});
await Promise.resolve();await Promise.resolve();
pending[3]([]);pending[4]([]);
const created=await creating;
assert.equal(created.id,'created-in-background');
assert.equal(state.convId,null,'A delayed new-chat response must not override a newer selection');
''')


def test_failed_attachment_leaves_uploading_state_and_offers_recovery():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const cards=[];
const bar={appendChild:card=>cards.push(card)};
globalThis.document={getElementById:()=>bar,dispatchEvent(){},createElement:()=>({
  attributes:{}, classes:new Set(), classList:{},
  setAttribute(key,value){this.attributes[key]=value;},
  querySelector:()=>({addEventListener(){}}),
})};
globalThis.CustomEvent=class {};
const dependencies={'./api.js':{api:{}},'./icons.js':{ICONS:{}},
  './format.js':{escapeHtml:s=>s,formatBytes:()=>'',fileExtensionLabel:()=>''}};
const module=new vm.SourceTextModule(readFileSync('static/js/chat_attachments.js','utf8'));
await module.link(specifier=>new vm.SyntheticModule(Object.keys(dependencies[specifier]),function(){
  for(const [key,value] of Object.entries(dependencies[specifier])) this.setExport(key,value);
}));
await module.evaluate();
let finish;
module.namespace.restorePendingAttachments([{kind:'image',previewUrl:'data:',name:'image.png',
  uploadPromise:new Promise(resolve=>finish=resolve)}]);
const card=cards[0];
assert(card.className.includes('is-uploading'));
assert.equal(card.attributes['aria-busy'],'true');
card.classList.remove=name=>card.classes.delete(name);
card.classList.toggle=(name,on)=>on?card.classes.add(name):card.classes.delete(name);
card.classes.add('is-uploading');
finish(null);
await Promise.resolve();
assert.equal(card.classes.has('is-uploading'),false);
assert.equal(card.classes.has('is-error'),true);
assert.equal(card.attributes['aria-busy'],'false');
assert(card.title.includes('try again'));
''')


def test_folder_picker_blocks_save_and_ignores_results_for_another_editor():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const controls = new Map();
const element = id => {
  if (!controls.has(id)) controls.set(id, {
    disabled:false, value:'', textContent:'', innerHTML:'', open:false,
    handlers:{}, addEventListener(name,handler){this.handlers[name]=handler;}, querySelectorAll(){return [];},
    classList:{contains(){return true;}},
  });
  return controls.get(id);
};
const document = {
  addEventListener(){}, getElementById:element,
  querySelector:element,
  querySelectorAll(){return [element('folder-name-input'),element('folder-mount-add')];},
};
let resolvePicker;
const requests=[];
const modules = {
  './api.js':{api:{post:async (url,body)=>{
    requests.push({url,body});
    return await new Promise(resolve => {resolvePicker=resolve;});
  }}},
  './state.js':{state:{}, STORAGE_KEYS:{}}, './storage.js':{storage:{}},
  './renderer.js':{clearMessages(){},renderAllMessages(){}},
  './ui.js':{toggleSidebar(){},openModal(){},closeModal(){},showToast(){}},
  './icons.js':{ICONS:{}}, './format.js':{escapeHtml:value=>value,formatConversationDate:()=>''},
  './file_panel.js':{refreshFilePanel(){},resetFilePanel(){}},
  './conversation_activity.js':{activityVersion:0,workingTitleAttributes:()=>'',syncConversationActivity(){}},
};
const context=vm.createContext({document,requestAnimationFrame(){},console});
const source=readFileSync('static/js/conversations.js','utf8')+
 '\nexport {openFolderEditor, browseFolderMount, submitFolderEditor, addFolderMountPath, editingMounts};';
const mod=new vm.SourceTextModule(source,{context});
await mod.link(specifier=>{
 const exports=modules[specifier];
 return new vm.SyntheticModule(Object.keys(exports),function(){
   for(const [name,value] of Object.entries(exports))this.setExport(name,value);
 },{context});
});
await mod.evaluate();
const {openFolderEditor,browseFolderMount,submitFolderEditor}=mod.namespace;
openFolderEditor(); element('folder-name-input').value='Original';
const picking=browseFolderMount();
assert.equal(element('folder-modal-confirm').disabled,true);
await submitFolderEditor();
assert.equal(requests.length,1,'Save must not race the native directory chooser');
openFolderEditor({id:'another',name:'Another',mounts:[]});
resolvePicker({path:'/unexpected-directory'});
await picking;
assert.equal(element('folder-mount-path').value,'');
assert.equal(element('folder-modal-confirm').disabled,false);
assert.equal(element('folder-name-input').disabled,false);
// The same two list listeners handle controls created on every re-render.
element('folder-mount-path').value='/chosen';
mod.namespace.addFolderMountPath();
element('folder-mount-list').handlers.change({target:{matches:()=>true,dataset:{mountReadonly:'0'},checked:false}});
assert.equal(mod.namespace.editingMounts[0].read_only,false);
element('folder-mount-list').handlers.click({target:{closest:()=>({dataset:{mountRemove:'0'}})}});
assert.equal(mod.namespace.editingMounts.length,0);
''')


def test_voice_errors_preserve_draft_and_notify_composer():
    run_node(r'''
import assert from 'node:assert/strict';
const handlers={}; let recognition; let inputEvents=0;
const button={classList:{add(){},remove(){}},addEventListener:(name,fn)=>handlers[name]=fn};
const input={value:'Existing draft',dispatchEvent:event=>{assert.equal(event.type,'input');inputEvents++;}};
globalThis.document={getElementById:id=>id==='mic-btn'?button:input};
globalThis.window={SpeechRecognition:class {
  constructor(){recognition=this;}
  start(){this.starts=(this.starts||0)+1;}
  stop(){this.stops=(this.stops||0)+1;}
}};
const warn=console.warn;console.warn=()=>{};
const {initVoiceInput}=await import('./static/js/voice.js');
initVoiceInput();
handlers.click();
handlers.click();
assert.equal(recognition.starts,1,'A second click before onstart must not start another session');
assert.equal(recognition.stops,1);
recognition.onstart();
recognition.onresult({resultIndex:0,results:[Object.assign([{transcript:'spoken text'}],{isFinal:true})]});
assert.equal(input.value,'Existing draft spoken text');
recognition.onerror({error:'network'});
recognition.onend();
assert.equal(input.value,'Existing draft spoken text','onerror must not clear text before onend commits it');
assert.equal(inputEvents,2,'Speech updates must run normal input validation and send-button updates');
recognition.start=()=>{throw new Error('Cannot start');};
handlers.click();
assert.equal(input.value,'Existing draft spoken text');
console.warn=warn;
''')


def test_composer_preserves_draft_during_streaming_and_ime_composition():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const elements=new Map();const sent=[];
const element=id=>{
 if(!elements.has(id))elements.set(id,{value:'',handlers:{},addEventListener(name,fn){this.handlers[name]=fn;}});
 return elements.get(id);
};
const state={isStreaming:true,enterToSend:true};
const input=element('user-input');input.value='Keep my draft';
const noop=()=>{};
const context=vm.createContext({state,document:{getElementById:element,addEventListener:noop},
 hasPendingAttachments:()=>false,autoResize:noop,updateCharCount:noop,
 sendMessage:async text=>sent.push(text),showToast:noop,stopAssistantTurn:noop,
 initImageAttachments:noop,initVoiceInput:noop,renameConversationTitle:noop,
 editAndResend:noop,regenerateFrom:noop,switchBranch:noop,persistConversationFor:noop,
});
const source=readFileSync('static/js/app.js','utf8');
new vm.Script(source.slice(source.indexOf('function bindInputEvents()'),source.indexOf('function bindKeyboardEvents()'))+'\nbindInputEvents();').runInContext(context);
const enter=isComposing=>input.handlers.keydown({key:'Enter',isComposing,preventDefault:noop});
assert.equal(element('send-btn').disabled,true);
enter(false);
assert.equal(input.value,'Keep my draft');assert.equal(sent.length,0);
state.isStreaming=false;
enter(true);
assert.equal(input.value,'Keep my draft');assert.equal(sent.length,0);
enter(false);
assert.equal(input.value,'');assert.deepEqual(sent,['Keep my draft']);
''')


def test_sidebar_ignores_stale_lists_and_preserves_state_after_mutation_failure():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const pending=[],errors=[];let renders=0;
const list={set innerHTML(value){renders++;},get innerHTML(){return '';}};
const state={convId:'current'};
const document={getElementById:id=>id==='conv-list'?list:null,addEventListener(){}};
const dependencies={
 './api.js':{api:{get:()=>new Promise(resolve=>pending.push(resolve))}},
 './state.js':{state,STORAGE_KEYS:{}},'./storage.js':{storage:{}},
 './renderer.js':{clearMessages(){},renderAllMessages(){}},
 './ui.js':{toggleSidebar(){},openModal(){},closeModal(){},showToast:error=>errors.push(error)},
 './icons.js':{ICONS:{}},'./format.js':{escapeHtml:s=>s,formatConversationDate:()=>''},
 './file_panel.js':{refreshFilePanel(){},resetFilePanel(){}},
 './conversation_activity.js':{activityVersion:0,workingTitleAttributes:()=>'',syncConversationActivity(){}},
};
const context=vm.createContext({document});
const mod=new vm.SourceTextModule(readFileSync('static/js/conversations.js','utf8')+'\nexport {conversations,applyMutation};',{context});
await mod.link(specifier=>new vm.SyntheticModule(Object.keys(dependencies[specifier]),function(){
 for(const [key,value] of Object.entries(dependencies[specifier]))this.setExport(key,value);
},{context}));
await mod.evaluate();
const first=mod.namespace.loadConversationList();const second=mod.namespace.loadConversationList();
pending[2]([]);pending[3]([]);await second;
const writes=renders;
pending[0]([{id:'outdated'}]);pending[1]([]);await first;
assert.equal(renders,writes);assert.equal(mod.namespace.conversations.length,0);
assert.equal(await mod.namespace.applyMutation(Promise.resolve({error:'Folder runtime is busy'})),null);
assert.equal(await mod.namespace.applyMutation(Promise.reject(new Error('Offline'))),null);
assert.deepEqual(errors,['Folder runtime is busy','Offline']);
assert.equal(state.convId,'current');
''')


def test_setup_build_log_retention_is_bounded_without_rewriting_history():
    run_node(r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const details={childNodes:[],appendChild(node){
 node.remove=()=>this.childNodes.splice(this.childNodes.indexOf(node),1);this.childNodes.push(node);
},get firstChild(){return this.childNodes[0];}};
const source=readFileSync('templates/startup_requirements.html','utf8');
const context=vm.createContext({details,document:{createTextNode:text=>({text})},render(){}});
new vm.Script(source.slice(source.indexOf('function processBuildEvent('),source.indexOf('async function buildImage()'))).runInContext(context);
for(let index=0;index<200;index++)context.processBuildEvent('event: log\ndata: '+JSON.stringify({line:`${index}:`+'x'.repeat(20000)}));
assert.equal(details.childNodes.length,50);
assert(details.childNodes.every(node=>node.text.length<=8193));
''')
