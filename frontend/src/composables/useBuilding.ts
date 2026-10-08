import { computed } from "vue";
import { useStore } from "vuex";
import { loadPlatformConfig, platformConfig } from "@/core/platform";

// Whether the current user may create worlds and should see the Build menu.
// Staff can always build; everyone else depends on the site's building switch.
// Builders keep access to worlds they already author or were added to.
export function useBuilding() {
  const store = useStore();
  loadPlatformConfig().catch(() => {});

  const isStaff = computed(() => !!store.state.auth.user?.is_staff);
  const canCreateWorlds = computed(() => {
    if (!store.getters.isAuthenticated || store.state.auth.user?.is_temporary) return false;
    return isStaff.value || !!platformConfig.value?.building_enabled;
  });

  return { isStaff, canCreateWorlds };
}
