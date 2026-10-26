import { block, fileHeader, pathLabel, icon, status, resultBlock } from './shared.js';
export default {
  name: 'Write', usingLabel: 'Writing file',
  icon: icon('<path d="M14 2H6a2 2 0 0 0-2 2v16h16V8zM14 2v6h6M8 14h8m-4-4v8"/>'),
  label: args => pathLabel('Write', args),
  status: result => status(result, 'Written'),
  renderResult: resultBlock,
  renderBody(args, result) {
    const content = String(args.content ?? '');
    const lines = content ? content.split('\n').length - (content.endsWith('\n') ? 1 : 0) : 0;
    const bytes = new TextEncoder().encode(content).length;
    return `${fileHeader(args)}<div class="native-toolbar"><span>${lines} lines · ${bytes.toLocaleString()} bytes</span></div>${this.renderResult(result)}<details class="native-details"><summary>Content supplied to Write</summary>${block('UTF-8 text', content || '(Empty file)')}</details>`;
  },
};
