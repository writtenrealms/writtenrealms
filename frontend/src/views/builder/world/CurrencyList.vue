<template>
  <div class="currency-page">
    <div class="currency-header">
      <div>
        <h2>CURRENCIES</h2>
        <p class="currency-intro color-text-60">
          Define the money used by this world. Currency codes are permanent identifiers;
          names, starting balances, and the default currency can be changed later.
        </p>
      </div>
      <button
        v-if="canManage && !showForm"
        class="btn-small"
        type="button"
        @click="startCreate"
      >
        ADD CURRENCY
      </button>
    </div>

    <div v-if="isInstanceWorld" class="inheritance-note panel">
      <div>
        This instance inherits its economy from
        <strong>{{ inheritedWorld.name }}</strong>. Currency definitions are read-only here.
      </div>
      <router-link
        :to="{
          name: 'builder_world_currency_list',
          params: { world_id: inheritedWorld.id },
        }"
      >
        Manage {{ inheritedWorld.name }} currencies
      </router-link>
    </div>

    <div v-else-if="!canManage" class="panel color-text-60">
      You do not have permission to manage currencies for this world.
    </div>

    <form v-if="showForm" class="currency-form panel panel-edit" @submit.prevent="saveCurrency">
      <div class="form-heading">
        <h3>{{ editingCurrency ? `EDIT ${editingCurrency.name.toUpperCase()}` : "ADD CURRENCY" }}</h3>
        <button class="btn-thin" type="button" :disabled="submitting" @click="cancelForm">
          CANCEL
        </button>
      </div>

      <div class="form-grid">
        <div class="form-group">
          <label for="currency-code">Code</label>
          <input
            id="currency-code"
            v-model.trim="form.code"
            type="text"
            maxlength="64"
            autocomplete="off"
            :readonly="Boolean(editingCurrency)"
            required
          />
          <div class="field-help color-text-50">
            Used in manifests and commands. Lowercase letters, numbers, underscores, and hyphens.
          </div>
        </div>

        <div class="form-group">
          <label for="currency-name">Singular name</label>
          <input
            id="currency-name"
            v-model.trim="form.name"
            type="text"
            maxlength="80"
            autocomplete="off"
            required
          />
          <div class="field-help color-text-50">For example: Obol.</div>
        </div>

        <div class="form-group">
          <label for="currency-plural-name">Plural name</label>
          <input
            id="currency-plural-name"
            v-model.trim="form.plural_name"
            type="text"
            maxlength="80"
            autocomplete="off"
          />
          <div class="field-help color-text-50">For example: Obols. Leave blank to reuse the singular name.</div>
        </div>

        <div class="form-group">
          <label for="currency-starting-amount">Starting balance</label>
          <input
            id="currency-starting-amount"
            v-model.number="form.starting_amount"
            type="number"
            min="0"
            :max="maxCurrencyAmount"
            step="1"
            required
          />
          <div class="field-help color-text-50">How much a newly created or reset player receives.</div>
        </div>
      </div>

      <div class="form-group description-field">
        <label for="currency-description">Description</label>
        <textarea
          id="currency-description"
          v-model.trim="form.description"
          maxlength="500"
          placeholder="Optional builder-facing notes about this currency."
        ></textarea>
      </div>

      <div v-if="formError" class="form-error" role="alert">{{ formError }}</div>

      <button class="btn-small" type="submit" :disabled="submitting">
        {{ submitting ? "SAVING..." : "SAVE CURRENCY" }}
      </button>
    </form>

    <div v-if="loading" class="currency-state color-text-60" aria-live="polite">
      Loading currencies...
    </div>

    <div v-else-if="loadError" class="currency-state load-error" role="alert">
      <div>{{ loadError }}</div>
      <button class="btn-thin" type="button" @click="loadCurrencies">RETRY</button>
    </div>

    <div v-else-if="currencies.length" class="currency-list">
      <article
        v-for="currency in sortedCurrencies"
        :key="currency.id"
        class="currency-card panel"
        :class="{ 'is-default': currency.is_default }"
      >
        <div class="currency-card-main">
          <div class="currency-card-heading">
            <div>
              <div class="currency-name-row">
                <h3>{{ currency.name }}</h3>
                <span v-if="currency.is_default" class="default-badge">DEFAULT</span>
              </div>
              <div class="currency-code">{{ currency.code }}</div>
            </div>

            <div v-if="canManage" class="currency-actions">
              <button class="btn-thin" type="button" :disabled="busy" @click="startEdit(currency)">
                EDIT
              </button>
              <button
                v-if="!currency.is_default"
                class="btn-thin"
                type="button"
                :disabled="busy"
                @click="setDefault(currency)"
              >
                MAKE DEFAULT
              </button>
              <button
                class="btn-thin delete-button"
                type="button"
                :disabled="busy || !currency.can_delete"
                :title="currency.can_delete ? 'Delete currency' : usageExplanation(currency)"
                @click="removeCurrency(currency)"
              >
                DELETE
              </button>
            </div>
          </div>

          <p v-if="currency.description" class="currency-description">
            {{ currency.description }}
          </p>

          <dl class="currency-facts">
            <div>
              <dt>Plural</dt>
              <dd>{{ currency.plural_name || currency.name }}</dd>
            </div>
            <div>
              <dt>Starting balance</dt>
              <dd>{{ currency.starting_amount }}</dd>
            </div>
          </dl>

          <div v-if="currency.usage.length" class="currency-usage color-text-60">
            In use by {{ usageSummary(currency) }}.
            <span v-if="canManage">Remove those references before deleting this currency.</span>
          </div>
          <div v-else class="currency-usage color-text-50">No authored or player references.</div>
        </div>
      </article>
    </div>

    <div v-else class="currency-state panel">
      No currencies are defined. Add one before starting the world.
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import {
  createCurrency,
  currencyApiErrorMessage,
  deleteCurrency,
  fetchCurrencies,
  makeDefaultCurrency,
  updateCurrency,
} from "@/services/currencies";
import type { BuilderCurrency } from "@/services/currencies";

const store = useStore();
const route = useRoute();

const maxCurrencyAmount = Number.MAX_SAFE_INTEGER;
const world = computed(() => store.state.builder.world || {});
const inheritedWorld = computed(() => world.value.instance_of || {});
const isInstanceWorld = computed(() => Boolean(inheritedWorld.value.id));
const effectiveWorldId = computed(() => (
  route.params.world_id || world.value.id
));
const canManage = computed(() => (
  !isInstanceWorld.value && Number(world.value?.builder_info?.builder_rank || 0) > 2
));

const currencies = ref<BuilderCurrency[]>([]);
const loading = ref(false);
const loadError = ref("");
const submitting = ref(false);
const busy = ref(false);
const showForm = ref(false);
const formError = ref("");
const editingCurrency = ref<BuilderCurrency | null>(null);
const form = reactive({
  code: "",
  name: "",
  plural_name: "",
  description: "",
  starting_amount: 0,
});

const sortedCurrencies = computed(() => [...currencies.value].sort((left, right) => {
  if (left.is_default !== right.is_default) return left.is_default ? -1 : 1;
  return left.code.localeCompare(right.code);
}));

const resetForm = () => {
  form.code = "";
  form.name = "";
  form.plural_name = "";
  form.description = "";
  form.starting_amount = 0;
  formError.value = "";
  editingCurrency.value = null;
};

const loadCurrencies = async () => {
  if (!effectiveWorldId.value) return;
  loading.value = true;
  loadError.value = "";
  try {
    currencies.value = await fetchCurrencies(effectiveWorldId.value);
    if (
      world.value?.id &&
      String(world.value.id) === String(route.params.world_id || world.value.id)
    ) {
      store.commit("builder/world_update", {
        default_currency: currencies.value.find((currency) => currency.is_default)?.code || null,
        currencies: currencies.value.map((currency) => ({
          id: currency.id,
          code: currency.code,
          name: currency.name,
          plural_name: currency.plural_name || currency.name,
          description: currency.description,
          is_default: currency.is_default,
        })),
      });
    }
  } catch (error: unknown) {
    currencies.value = [];
    loadError.value = currencyApiErrorMessage(error, "Could not load currencies.");
  } finally {
    loading.value = false;
  }
};

watch(effectiveWorldId, () => {
  showForm.value = false;
  resetForm();
  loadCurrencies();
}, { immediate: true });

const startCreate = () => {
  resetForm();
  showForm.value = true;
};

const startEdit = (currency: BuilderCurrency) => {
  editingCurrency.value = currency;
  form.code = currency.code;
  form.name = currency.name;
  form.plural_name = currency.plural_name || "";
  form.description = currency.description || "";
  form.starting_amount = currency.starting_amount || 0;
  formError.value = "";
  showForm.value = true;
};

const cancelForm = () => {
  showForm.value = false;
  resetForm();
};

const invalidateWorldConfig = () => {
  store.commit("builder/worlds/config_clear");
};

const validateForm = () => {
  if (!editingCurrency.value && !/^[a-z][a-z0-9_-]{0,63}$/.test(form.code)) {
    return "Code must start with a lowercase letter and contain only lowercase letters, numbers, underscores, or hyphens.";
  }
  if (!form.name) return "A singular name is required.";
  if (!Number.isSafeInteger(form.starting_amount) || form.starting_amount < 0) {
    return "Starting balance must be a nonnegative whole number.";
  }
  return "";
};

const saveCurrency = async () => {
  const validationError = validateForm();
  if (validationError) {
    formError.value = validationError;
    return;
  }

  submitting.value = true;
  formError.value = "";
  const payload = {
    name: form.name,
    plural_name: form.plural_name,
    description: form.description,
    starting_amount: form.starting_amount,
  };
  try {
    if (editingCurrency.value) {
      await updateCurrency(effectiveWorldId.value, editingCurrency.value.id, {
        code: editingCurrency.value.code,
        ...payload,
      });
      store.commit("ui/notification_set", `${form.name} updated.`);
    } else {
      await createCurrency(effectiveWorldId.value, { code: form.code, ...payload });
      store.commit("ui/notification_set", `${form.name} created.`);
    }
    invalidateWorldConfig();
    cancelForm();
    await loadCurrencies();
  } catch (error: unknown) {
    formError.value = currencyApiErrorMessage(error, "Could not save currency.");
  } finally {
    submitting.value = false;
  }
};

const setDefault = async (currency: BuilderCurrency) => {
  if (!window.confirm(`Use ${currency.name} as this world's default currency?`)) return;
  busy.value = true;
  try {
    await makeDefaultCurrency(effectiveWorldId.value, currency.id);
    invalidateWorldConfig();
    store.commit("ui/notification_set", `${currency.name} is now the default currency.`);
    await loadCurrencies();
  } catch (error: unknown) {
    store.commit(
      "ui/notification_set_error",
      currencyApiErrorMessage(error, "Could not change the default currency."),
    );
  } finally {
    busy.value = false;
  }
};

const usageSummary = (currency: BuilderCurrency) => currency.usage
  .map((entry) => `${entry.count} ${entry.type}${entry.count === 1 ? "" : "s"}`)
  .join(", ");

const usageExplanation = (currency: BuilderCurrency) => currency.usage.length
  ? `In use by ${usageSummary(currency)}.`
  : "This currency cannot be deleted.";

const removeCurrency = async (currency: BuilderCurrency) => {
  if (!currency.can_delete) return;
  if (!window.confirm(`Delete ${currency.name}? Currency codes cannot be restored automatically.`)) return;
  busy.value = true;
  try {
    await deleteCurrency(effectiveWorldId.value, currency.id);
    invalidateWorldConfig();
    store.commit("ui/notification_set", `${currency.name} deleted.`);
    await loadCurrencies();
  } catch (error: unknown) {
    store.commit(
      "ui/notification_set_error",
      currencyApiErrorMessage(error, "Could not delete currency."),
    );
  } finally {
    busy.value = false;
  }
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

.currency-page {
  max-width: 900px;
}

.currency-header,
.form-heading,
.currency-card-heading,
.currency-name-row,
.currency-actions {
  display: flex;
  align-items: center;
}

.currency-header,
.form-heading,
.currency-card-heading {
  justify-content: space-between;
  gap: 18px;
}

.currency-intro {
  max-width: 680px;
  margin: 4px 0 20px;
}

.inheritance-note {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.currency-form {
  margin-bottom: 20px;
  padding: 18px;
}

.form-heading {
  margin-bottom: 18px;
}

.form-heading h3,
.currency-name-row h3 {
  margin: 0;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 18px;
}

.field-help {
  font-size: 12px;
  margin-top: 5px;
}

.description-field textarea {
  min-height: 86px;
}

.form-error,
.load-error {
  color: $color-red;
}

.form-error {
  margin: -2px 0 14px;
}

.currency-state {
  padding: 18px;
}

.load-error {
  display: flex;
  align-items: center;
  gap: 14px;
}

.currency-list {
  display: grid;
  gap: 12px;
}

.currency-card {
  padding: 18px;
}

.currency-card.is-default {
  border-color: rgba($color-secondary, 0.45);
}

.currency-name-row {
  gap: 10px;
}

.currency-code {
  color: $color-text-hex-50;
  font-family: monospace;
  margin-top: 4px;
}

.default-badge {
  color: $color-secondary;
  border: 1px solid rgba($color-secondary, 0.4);
  border-radius: 999px;
  font-size: 10px;
  letter-spacing: 0.08em;
  padding: 3px 8px;
}

.currency-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.delete-button:not(:disabled) {
  color: $color-red;
}

.currency-description {
  color: $color-text-70;
  margin: 14px 0;
  max-width: 700px;
}

.currency-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 18px 40px;
  margin: 14px 0;
}

.currency-facts div {
  min-width: 130px;
}

.currency-facts dt {
  color: $color-text-hex-50;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.currency-facts dd {
  margin: 3px 0 0;
}

.currency-usage {
  border-top: 1px solid $color-background-light-border;
  font-size: 12px;
  padding-top: 12px;
}

@media ($mobile-site) {
  .currency-header,
  .inheritance-note,
  .form-heading,
  .currency-card-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .form-grid {
    grid-template-columns: 1fr;
  }

  .currency-actions {
    justify-content: flex-start;
  }
}
</style>
