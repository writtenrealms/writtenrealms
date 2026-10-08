import axios from 'axios';
import { shallowRef } from 'vue';
import type { RouteLocationNormalized, RouteLocationRaw } from 'vue-router';

export interface PlatformConfig {
  main_world_id: number | null;
  // Whether any signed-up user may create worlds; staff always can.
  building_enabled: boolean;
}

// One request per page load, shared by navigation and the header. Reloading
// picks up operator changes without adding requests to gameplay/navigation.
export const platformConfig = shallowRef<PlatformConfig | null>(null);
let pending: Promise<PlatformConfig> | null = null;

export function loadPlatformConfig(): Promise<PlatformConfig> {
  if (platformConfig.value) return Promise.resolve(platformConfig.value);
  if (!pending) {
    pending = axios.get('/lobby/config/').then(({ data }) => {
      const id = data.main_world_id;
      if (id !== null && (!Number.isSafeInteger(id) || id < 1)) {
        throw new Error('Invalid main world configuration');
      }
      platformConfig.value = { main_world_id: id, building_enabled: data.building_enabled !== false };
      return platformConfig.value;
    }).finally(() => { pending = null; });
  }
  return pending;
}

export function siteEntryDestination(
  config: PlatformConfig,
  route: Pick<RouteLocationNormalized, 'name' | 'query' | 'hash' | 'fullPath'>,
  authenticated: boolean,
): RouteLocationRaw | null {
  if (config.main_world_id !== null) {
    return {
      name: 'lobby_world_details',
      params: { world_id: config.main_world_id },
      query: route.query,
      hash: route.hash,
    };
  }
  if (route.name === 'lobby' && !authenticated) {
    return { name: 'login', query: { redirect: route.fullPath } };
  }
  return null;
}
