// Workspace data actions.
import { api } from './api.js';

export async function deleteAllData() {
  try {
    const data = await api.post('/api/danger/delete-all', {});
    if (data?.ok) {
      return { ok: true, deleted: data.deleted ?? 0 };
    }
    return { ok: false, error: data?.error || data?.errors?.join('; ') || 'Unknown error' };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}
