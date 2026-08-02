<template>
  <div id="world-create">
    <h2>CREATE NEW WORLD</h2>

    <FormField
      :elementSchema="name_schema"
      v-model="world_name"
      :formErrors="formErrors"
      required="required"
      class='world-name'
    />

    <FormField
      :elementSchema="is_multi_schema"
      v-model="is_multi"
      :formErrors="formErrors"
      required="required"
      class='multiplayer-world'/>

    <div class="currency-heading">STARTING CURRENCY</div>
    <p class="currency-help color-text-60">
      This is the world's first and default currency. You can add more currencies later.
    </p>

    <FormField
      :elementSchema="currency_code_schema"
      v-model="currency_code"
      :formErrors="formErrors"
      required="required"
    />

    <FormField
      :elementSchema="currency_name_schema"
      v-model="currency_name"
      :formErrors="formErrors"
      required="required"
    />

    <FormField
      :elementSchema="currency_plural_schema"
      v-model="currency_plural_name"
      :formErrors="formErrors"
    />

    <button class="btn-medium" :disabled="creating" @click="create">
      {{ creating ? "CREATING..." : "CREATE" }}
    </button>
  </div>
</template>

<script lang='ts' setup>
import { ref } from "vue";
import { useStore } from "vuex";
import { useRouter } from "vue-router";
import FormField from "@/components/forms/FormField.vue";
import { FormElement } from "@/core/forms.ts";
import { builderRoomIndexRoute } from "@/core/builderRoutes";

const store = useStore();
const router = useRouter();

const world_name = ref("A New World");
const is_multi = ref(true);
const currency_code = ref("gold");
const currency_name = ref("Gold");
const currency_plural_name = ref("");
const creating = ref(false);
const formErrors = ref<Record<string, string[]>>({});

const name_schema: FormElement = {
  attr: "name",
  label: ""
};

const is_multi_schema: FormElement = {
  attr: "is_multiplayer",
  label: "Is Multiplayer",
  widget: "checkbox",
  default: false
};

const currency_code_schema: FormElement = {
  attr: "initial_currency_code",
  label: "Currency Code",
  help: "A permanent lowercase identifier used by manifests and commands, such as gold or obol."
};

const currency_name_schema: FormElement = {
  attr: "initial_currency_name",
  label: "Singular Name",
  help: "The name shown for exactly one unit, such as Obol."
};

const currency_plural_schema: FormElement = {
  attr: "initial_currency_plural_name",
  label: "Plural Name",
  help: "The name shown for other amounts, such as Obols. Leave blank to reuse the singular name."
};

const create = async () => {
  creating.value = true;
  formErrors.value = {};
  try {
    const world = await store.dispatch("builder/world_create", {
      name: world_name.value,
      is_multiplayer: is_multi.value,
      initial_currency_code: currency_code.value,
      initial_currency_name: currency_name.value,
      initial_currency_plural_name: currency_plural_name.value
    });
    store.commit(
      "ui/notification_set",
      `Successfully created ${world_name.value}`
    );
    router.push(builderRoomIndexRoute(world.id, world.last_viewed_room));
  } catch (error: any) {
    const data = error?.response?.data;
    if (data && typeof data === "object" && !Array.isArray(data)) {
      formErrors.value = Object.fromEntries(
        Object.entries(data).map(([key, value]) => [
          key,
          Array.isArray(value) ? value.map(String) : [String(value)],
        ]),
      );
    }
    const firstError = Object.values(formErrors.value)[0]?.[0] || "Could not create world.";
    store.commit("ui/notification_set_error", firstError);
  } finally {
    creating.value = false;
  }
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";
#world-create {
  max-width: 420px;
  margin: 0 auto;
  margin-top: auto;
  margin-bottom: auto;
  padding-bottom: 40px;

  .form-group.world-name {
    margin-top: 40px;
  }

  .form-group.multiplayer-world {
    margin: 20px 0 0 0;
  }

  .currency-heading {
    color: $color-secondary;
    font-size: 13px;
    letter-spacing: 0.08em;
    margin-top: 32px;
  }

  .currency-help {
    font-size: 13px;
    margin: 7px 0 18px;
  }

  button {
    width: 100%;
    margin-top: 40px;
  }
}
</style>
