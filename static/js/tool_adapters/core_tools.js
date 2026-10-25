/** Display labels for the built-in tool contract; schemas come from the server. */
import { registerAdapter } from './registry.js';

registerAdapter({ tools: ['bash'], usingLabel: 'Running command' });
registerAdapter({ tools: ['read'], usingLabel: 'Reading file' });
registerAdapter({ tools: ['write'], usingLabel: 'Writing file' });
registerAdapter({ tools: ['edit'], usingLabel: 'Editing file' });
