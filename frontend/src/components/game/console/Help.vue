<template>
  <div class="help indented">
    <div v-if="topicHelp">
      <div class="topic-help">
        <div class="name">
          {{ topicHelp.name }}
          -
          <span class="color-text-50 font-text-regular">{{ topicHelp.format }}</span>
        </div>

        <div class="description">{{ topicHelp.description }}</div>

        <template v-if="topicHelp.details && topicHelp.details.length">
          <div>Details:</div>
          <div class="details editable-box">
            <div class="detail" v-for="(detail, index) in topicHelp.details" :key="index">{{ detail }}</div>
          </div>
        </template>

        <template v-if="topicHelp.examples && topicHelp.examples.length">
          <div>Examples:</div>
          <div class="examples editable-box">
            <div class="example" v-for="(example, index) in topicHelp.examples" :key="index">{{ example }}</div>
          </div>
        </template>
      </div>
    </div>
    <div v-else-if="abilityHelp" class="help-text">{{ message.text }}</div>
    <div v-else-if="commandGroups">
      <div v-for="group in commandGroups" :key="group.title" class="cmd-group">
        <div class="group-title">{{ group.title }}</div>
        <div class="cmds">
          <button v-for="command in group.commands" :key="command" type="button" class="cmd"
            :aria-label="`Help for ${command}`" @click="cmdHelp(command)">{{ command }}</button>
        </div>
      </div>
    </div>
    <div v-else class="help-text">{{ message.text }}</div>
  </div>
</template>


<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { commandHelpGroups } from "@/core/commandHelp";

const store = useStore();
const props = defineProps<{ message: any }>();

const commandGroups = computed(() => {
  const commands = props.message?.data?.commands;
  return Array.isArray(commands) ? commandHelpGroups(commands) : null;
});
const topicHelp = computed(() => {
  const data = props.message?.data || {};
  return data.command || null;
});
const abilityHelp = computed(() => {
  const data = props.message?.data || {};
  return data.ability || null;
});
const cmdHelp = (cmd: string) => {
  store.dispatch("game/cmd", `help ${cmd}`);
}
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.help {
  .topic-help {
    .name,
    .description {
      margin-bottom: 8px;
    }

    .examples {
      margin-top: 3px;
    }

    .details {
      margin-top: 3px;
      margin-bottom: 8px;
    }
  }

  .help-text {
    white-space: pre-line;
  }

  .cmd-group {
    &:not(:last-child) {
      margin-bottom: 8px;
    }

    .group-title {
      color: $color-text-hex-50;
      @include font-text-regular;
    }
    .cmds {
      display: flex;
      flex-wrap: wrap;
      column-gap: 8px;

      .cmd {
        font: inherit;
        color: inherit;
        background: none;
        padding: 0;
        border: 0;
        border-bottom: 1px dotted $color-background-light-border-selected;
        cursor: pointer;
      }
    }
  }
}
</style>
