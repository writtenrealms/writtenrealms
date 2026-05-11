<template>
  <div id="mob-abilities">
    <h3>ABILITIES</h3>

    <div v-if="template.use_abilities" class="color-text-70">
      <div v-if="template.combat_script">
        <p>Use of abilities is enabled.</p>
        <p>In combat, this mob executes the following script:</p>
        <ol class="list color-text">
          <li v-for="(line, index) in template.combat_script.split('\n')" :key="index">{{ line }}</li>
        </ol>
      </div>
      <div v-else>
        <p>This mob is set to use abilities but does not specify a combat script. Include at least one ability command in the script.</p>
      </div>
    </div>

    <div v-else class="color-text-70">
      <p>This mob does not currently use abilities.</p>
    </div>

    <button class="btn-thin" @click="editAbilities">EDIT</button>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";

const store = useStore();

const template = computed(() => store.state.builder.worlds.mob_template);

const editAbilities = () => {
  const modal = {
    data: template.value,
    schema: [
      {
        attr: "use_abilities",
        label: "Use Abilities",
        widget: "checkbox",
        help: "Check this box if you want this mob to use abilities. Specify which abilities to use in the combat script below.",
      },
      {
        attr: "combat_script",
        label: "Combat Script",
        widget: "textarea",
        help: "This script is executed by the mob, line by line, when it is in combat. It will cycle back to the start after it has reached the end.",
      },
    ],
    action: "builder/worlds/mob_template_save",
  };
  store.commit("ui/modal/open_form", modal);
};
</script>
