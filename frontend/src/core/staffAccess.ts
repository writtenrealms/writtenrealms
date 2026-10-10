import type { Store } from 'vuex';

export async function requireStaff(store: Store<any>, to: { fullPath: string }) {
  const login = () => ({ path: '/login', query: { redirect: to.fullPath } });
  if (!store.getters.isAuthenticated) return login();

  try {
    const user = await store.dispatch('auth/refresh_user');
    if (!store.getters.isAuthenticated) return login();
    if (user?.is_staff === true) return true;
    store.commit('ui/notification_set_error', 'Access denied.');
  } catch {
    if (!store.getters.isAuthenticated) return login();
    store.commit('ui/notification_set_error', 'Could not verify staff access. Please try again.');
  }
  return '/lobby';
}
