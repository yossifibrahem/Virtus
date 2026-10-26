import { block, fileHeader, pathLabel, icon, status, resultBlock } from './shared.js';
export default {
  name: 'Edit', usingLabel: 'Editing file',
  icon: icon('<path d="m16 3 5 5-12 12-6 1 1-6zM13 6l5 5M4 15l5 5"/>'),
  label: args => pathLabel('Edit', args),
  status: result => status(result, 'Edited'),
  renderResult: resultBlock,
  renderBody(args, result) {
    return `${fileHeader(args)}<div class="native-toolbar"><span>${args.replace_all === true ? 'Replace all exact matches' : 'Replace one exact match'}</span></div>${this.renderResult(result)}<details class="native-details" open><summary>Requested replacement</summary><div class="native-replacement">${block('− Find exact text', args.old_text, 'native-before')}${block('+ Replace with', args.new_text === '' ? '(Delete matching text)' : args.new_text, 'native-after')}</div></details>`;
  },
};
