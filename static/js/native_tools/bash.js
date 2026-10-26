import { textOf, failed, block, icon } from './shared.js';

function outcome(result) {
  const text = textOf(result);
  const match = text.match(/\n?\[exit status (-?\d+)\]\s*$/);
  return { output: match ? text.slice(0, match.index) : text, exit: match ? Number(match[1]) : null };
}
export default {
  name: 'Bash', usingLabel: 'Running command',
  icon: icon('<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7 9 3 3-3 3m6 0h4"/>'),
  label: args => args.command ? `Bash · ${String(args.command).split('\n')[0]}` : 'Bash',
  status(result) {
    const { exit } = outcome(result);
    if (failed(result)) return { label: /cancelled/i.test(textOf(result)) ? 'Cancelled' : /timed out/i.test(textOf(result)) ? 'Timed out' : 'Failed', tone: 'error' };
    return { label: exit === null ? 'Finished' : `Exit ${exit}`, tone: exit === null ? 'neutral' : exit === 0 ? 'success' : 'error' };
  },
  renderResult(result) {
    const { output } = outcome(result);
    return block(failed(result) ? 'Could not complete' : 'Output', output || '(No output)', failed(result) ? 'native-error' : 'native-terminal');
  },
  renderBody(args, result) {
    return `<div class="native-toolbar"><span>Sandbox shell · ${Number.isInteger(args.timeout) ? args.timeout : 120}s timeout</span></div>${block('Command', args.command, 'native-terminal')}${this.renderResult(result)}`;
  },
};
