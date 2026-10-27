import { block, icon, status, resultBlock, textOf } from './shared.js';
export default {
  name: 'Memory', usingLabel: 'Updating memory',
  icon: icon('<path d="M9 3H5v18h14V3h-4M9 3v4h6V3zM8 12h8M8 16h6"/>'),
  label: () => 'Update memory',
  status(result) {
    try {
      if (JSON.parse(textOf(result)).error) return {label: 'Conflict', tone: 'error'};
    } catch { /* Plain error responses use the shared status parser. */ }
    return status(result, 'Remembered');
  },
  renderResult: resultBlock,
  renderBody(args, result) {
    return `${this.renderResult(result)}<details class="native-details"><summary>Memory update</summary>${block('Remembered facts', args.content ?? '')}</details>`;
  },
};
