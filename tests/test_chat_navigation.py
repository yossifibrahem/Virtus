"""Run the browser send handler with navigation during an in-flight reply."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("destination", ["empty", "other", "same"])
def test_reply_completion_keeps_original_conversation_history(destination):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    script = r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const destination = process.argv[1];
const state = {
  convId: 'original', isStreaming: false,
  messages: [{role: 'user', content: 'Earlier question'}],
  displayLog: [{type: 'message', role: 'user', content: 'Earlier question'}],
};
const saved = new Map();
let branchSyncs = 0;
let started;
let complete;
const streamingStarted = new Promise(resolve => started = resolve);
const replyComplete = new Promise(resolve => complete = resolve);
const visible = turn => state.convId === turn.convId;
const sync = turn => {
  if (visible(turn)) {
    state.messages = turn.messages;
    state.displayLog = turn.displayLog;
  }
};
const persist = async turn => {
  // Simulate the JSON request/response boundary and reload from saved storage.
  saved.set(turn.convId, structuredClone(turn));
};
const deps = {
  hasPendingAttachments: () => false,
  createTurnContext: convId => ({
    convId, messages: state.messages.slice(), displayLog: state.displayLog.slice(),
  }),
  setStreaming: active => state.isStreaming = active,
  syncVisibleTurn: sync,
  isTurnVisible: visible,
  persistTurnConversation: persist,
  runAssistantTurnAndPersist: async turn => {
    started();
    await replyComplete;
    // assistant_done replaces the arrays with the backend's completed history.
    turn.messages = [...turn.messages, {role: 'assistant', content: 'Completed answer'}];
    turn.displayLog = [...turn.displayLog, {type: 'message', role: 'assistant', content: 'Completed answer'}];
    await persist(turn);
    sync(turn);
    if (visible(turn)) state.isStreaming=false;
  },
};
const modules = {
  './state.js': {state},
  './renderer.js': {appendMessage() {}},
  './chat_branches.js': {syncVisibleBranches() { branchSyncs++; }},
  './file_panel.js': {refreshFilePanel: async () => {}},
  './chat_attachments.js': {
    drainPendingAttachments: () => [],
    restorePendingAttachments() {},
    refreshImagePreviewBar() {},
    uploadConversationFiles: async () => [],
    waitForPendingAttachmentAdds: async () => {},
  },
};
const module = new vm.SourceTextModule(readFileSync('static/js/chat_send.js', 'utf8'));
await module.link(specifier => {
  const exports = modules[specifier];
  assert(exports, `Unexpected import ${specifier}`);
  return new vm.SyntheticModule(Object.keys(exports), function() {
    for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
  });
});
await module.evaluate();
const sending = module.namespace.sendMessage('New question', deps);
await streamingStarted;
if (destination !== 'same') {
  state.convId = destination === 'empty' ? null : 'another';
  state.messages = destination === 'empty' ? [] : [{role: 'user', content: 'Other chat'}];
  state.displayLog = destination === 'empty' ? [] : [{type: 'message', role: 'user', content: 'Other chat'}];
}
const destinationMessages = structuredClone(state.messages);
const destinationLog = structuredClone(state.displayLog);
complete();
await sending;
const reloaded = saved.get('original');
assert.deepEqual(reloaded.messages.map(msg => msg.content), ['Earlier question', 'New question', 'Completed answer']);
assert.deepEqual(reloaded.displayLog.map(entry => entry.content), ['Earlier question', 'New question', 'Completed answer']);
if (destination !== 'same') {
  assert.deepEqual(state.messages, destinationMessages);
  assert.deepEqual(state.displayLog, destinationLog);
  assert.equal(branchSyncs, 1, 'Background completion must not synchronize another chat branches');
} else {
  assert.deepEqual(state.messages, reloaded.messages);
  assert.deepEqual(state.displayLog, reloaded.displayLog);
  assert.equal(branchSyncs, 2);
}

// Composer ownership starts synchronously, before attachment reads and creating
// a new conversation. Fast duplicate sends cannot create a second chat.
state.isStreaming=false;state.convId=null;state.messages=[];state.displayLog=[];
let finishCreation;let creations=0;
deps.createNewConversation=()=>{
 creations++;return new Promise(resolve=>finishCreation=()=>{state.convId='created';resolve({id:'created'});});
};
const firstSend=module.namespace.sendMessage('Only once',deps);
assert(state.isStreaming);
await module.namespace.sendMessage('Duplicate',deps);
assert.equal(creations,1);
finishCreation();await firstSend;
assert.deepEqual(saved.get('created').messages.map(message=>message.content),['Only once','Completed answer']);
assert.equal(state.isStreaming,false);

// The assistant releases its composer before the final branch save. A newer
// turn that starts while that save is pending must retain its busy state.
state.convId='late-save';state.isStreaming=false;state.messages=[];state.displayLog=[];
let savedFinal,releaseFinal;let writes=0;
const finalSaveStarted=new Promise(resolve=>savedFinal=resolve);
deps.persistTurnConversation=async turn=>{
 await persist(turn);
 if(++writes===2){savedFinal();await new Promise(resolve=>releaseFinal=resolve);}
};
const saving=module.namespace.sendMessage('Late save',deps);
await finalSaveStarted;
assert.equal(state.isStreaming,false);
state.isStreaming=true;
releaseFinal();await saving;
assert.equal(state.isStreaming,true,'An older final save must not release a newer turn composer');

// A failed save releases the composer even though the assistant never starts.
state.convId='created';state.isStreaming=false;
deps.persistTurnConversation=async()=>{throw new Error('Storage unavailable');};
await assert.rejects(module.namespace.sendMessage('Still editable',deps),/Storage unavailable/);
assert.equal(state.isStreaming,false);
state.convId=null;
deps.createNewConversation=async()=>{throw new Error('Creation failed');};
await assert.rejects(module.namespace.sendMessage('Retry creation',deps),/Creation failed/);
assert.equal(state.isStreaming,false);
'''
    result = subprocess.run(
        [node, "--experimental-vm-modules", "--input-type=module", "-e", script, destination],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
