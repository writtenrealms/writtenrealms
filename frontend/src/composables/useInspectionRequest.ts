import { onBeforeUnmount, ref, shallowRef } from 'vue';
import axios, { type AxiosRequestConfig } from 'axios';

// Each reader owns one request. Navigation and edits discard stale responses.
export function useInspectionRequest<T = any>() {
  const data = shallowRef<T | null>(null);
  const loading = ref(false);
  const error = ref('');
  let controller: AbortController | null = null;
  let revision = 0;
  function clear() {
    revision++;
    controller?.abort();
    data.value = null;
    error.value = '';
    loading.value = false;
  }
  async function load(url: string, options: AxiosRequestConfig = {}) {
    clear();
    const current = revision;
    controller = new AbortController();
    loading.value = true;
    try {
      const response = await axios({ url, ...options, signal: controller.signal });
      if (current === revision) data.value = response.data;
    } catch (cause: any) {
      if (current === revision && !axios.isCancel(cause)) {
        const status = cause.response?.status;
        error.value = status === 404 ? 'This mob or world is no longer available. It may have despawned or been reset.'
          : status === 403 ? 'You do not have access to this information.'
          : status === 401 ? 'Please sign in again to inspect this world.'
          : cause.response?.data?.detail || 'Could not load this information. Please try again.';
      }
    } finally {
      if (current === revision) loading.value = false;
    }
  }
  onBeforeUnmount(clear);
  return { data, loading, error, load, clear };
}
