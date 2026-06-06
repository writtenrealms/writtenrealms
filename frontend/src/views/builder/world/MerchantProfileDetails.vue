<template>
  <div id="merchant-profile-details">
    <div v-if="isLoading" class="color-text-60">Loading merchant profile...</div>
    <template v-else-if="merchantProfile">
      <div class="merchant-profile-header mb-4">
        <div>
          <h2>{{ merchantProfile.name }}</h2>
          <div class="color-text-60">
            ID: {{ merchantProfile.id }} | Slug: {{ merchantProfile.slug }} | Stock: {{ merchantProfile.stock_count }}
          </div>
        </div>

        <div class="merchant-profile-actions">
          <button class="btn-small" :disabled="!merchantProfile.yaml" @click="copyYaml">
            COPY YAML
          </button>
          <button class="btn-small" :disabled="!merchantProfile.yaml" @click="editYaml">
            EDIT
          </button>
        </div>
      </div>

      <textarea
        :value="merchantProfile.yaml || ''"
        class="manifest-output"
        readonly
        spellcheck="false"
      />
    </template>
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";

const route = useRoute();
const router = useRouter();
const store = useStore();

const merchantProfile = ref<any | null>(null);
const isLoading = ref(false);
const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/merchantprofiles/${route.params.merchant_profile_id}/`
));

const extractError = (error: any): string => {
  const data = error?.response?.data;
  if (!data) return "Could not load merchant profile.";
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return data[0] || "Could not load merchant profile.";
  if (typeof data === "object") {
    if (typeof data.detail === "string") return data.detail;
    const firstKey = Object.keys(data)[0];
    const value = data[firstKey];
    if (Array.isArray(value)) return value[0];
    if (typeof value === "string") return value;
  }
  return "Could not load merchant profile.";
};

const fetchMerchantProfile = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value);
    merchantProfile.value = resp.data;
  } catch (error: any) {
    merchantProfile.value = null;
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const copyYaml = async () => {
  try {
    await navigator.clipboard.writeText(merchantProfile.value?.yaml || "");
    store.commit("ui/notification_set", "Merchant profile YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy YAML to clipboard.");
  }
};

const editYaml = () => {
  if (!merchantProfile.value) return;
  router.push({
    name: "builder_world_edit",
    params: {
      world_id: route.params.world_id,
    },
    query: {
      prefill: "merchant-profile",
      merchant_profile_id: merchantProfile.value.id,
    },
  });
};

onMounted(fetchMerchantProfile);

watch(
  () => route.params.merchant_profile_id,
  async (nextValue, prevValue) => {
    if (nextValue === prevValue) return;
    await fetchMerchantProfile();
  },
);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

#merchant-profile-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;

  .merchant-profile-header {
    align-items: flex-start;
    display: flex;
    gap: 1rem;
    justify-content: space-between;
  }

  .merchant-profile-actions {
    display: flex;
    flex-shrink: 0;
    gap: 0.5rem;
  }

  .manifest-output {
    box-sizing: border-box;
    width: 100%;
    min-height: 640px;
    padding: 0.75rem;
    border: 1px solid $color-form-border;
    background: $color-background;
    color: $color-text;
    font-family: monospace;
    line-height: 1.35;
  }
}
</style>
