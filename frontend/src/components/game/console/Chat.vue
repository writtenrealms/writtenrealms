<template>
  <div v-if="message" :class="{ echo: message.echo }">
    <div v-for="(line, index) in lines" :key="index" :class="{ echo: message.echo, builder: isBuilder }">
      <span v-if="index === 0 && isBuilder" class="mr-2">[ Builder ]</span>
      <span class="line"><template v-for="(part, partIndex) in line" :key="partIndex"><a
        v-if="part.href"
        :href="part.href"
        class="interactive"
        target="_blank"
        rel="noopener noreferrer"
      >{{ part.text }}</a><template v-else>{{ part.text }}</template></template></span><button
        v-if="index === lines.length - 1 && questionId !== null"
        type="button"
        class="question-id"
        :title="`Answer question ${questionId}`"
        :aria-label="`Answer question ${questionId}`"
        @click.stop="prepareAnswer"
      >{{ `[ ${questionId} ]` }}</button>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed } from 'vue';
import { useStore } from 'vuex';

const props = defineProps<{
  message: any;
}>();
const store = useStore();

const isBuilder = computed(() => Boolean(
  props.message.data?.is_builder ?? props.message.data?.actor?.is_builder,
));
const questionId = computed(() => {
  if (props.message.type !== 'notification.cmd.ask.success') return null;
  const id = props.message.data?.question_id;
  return Number.isSafeInteger(id) && id > 0 ? id : null;
});
const prepareAnswer = () => {
  if (questionId.value === null) return;
  if (store.state.ui?.modal?.isOpen) store.commit('ui/modal/close');
  store.commit('game/command_draft_set', `answer ${questionId.value} `);
};

// Keep player text in Vue text nodes. Only http(s) tokens become links; no
// message content is interpreted as HTML, including markup inside a URL.
const linkParts = (line: string) => {
  const parts: { text: string; href?: string }[] = [];
  let cursor = 0;
  for (const match of line.matchAll(/https?:\/\/[^\s<>"']+/g)) {
    const url = match[0].replace(/[.,!?;:)\]}]+$/, "");
    try {
      if (!new URL(url).hostname) continue;
    } catch {
      continue;
    }
    const index = match.index!;
    if (index > cursor) parts.push({ text: line.slice(cursor, index) });
    parts.push({ text: url, href: url });
    cursor = index + url.length;
  }
  if (cursor < line.length) parts.push({ text: line.slice(cursor) });
  return parts;
};

const lines = computed(() => {
  let text = String(props.message.text ?? "");
  const suffix = questionId.value === null ? "" : ` [ ${questionId.value} ]`;
  if (suffix && text.endsWith(suffix)) text = text.slice(0, -suffix.length);
  return text.split("\n").map(linkParts);
});
</script>

<style lang='scss' scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
.echo {
  color: $color-text-hex-50;
}
.builder {
  color: $color-primary;
  @include font-text-regular;
}
.line {
  overflow-wrap: anywhere;
  :deep(a) {
    color: $color-text-70;

    &:hover {
      color: $color-text;
      text-decoration: none;
      border-bottom-color: #aaa;
      cursor: pointer;
    }
  }
}
.question-id {
  margin-left: 0.25em;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: $color-text-hex-60;
  white-space: nowrap;
  cursor: pointer;

  &:hover { text-decoration: underline; }
  &:focus-visible { outline: 1px solid currentColor; outline-offset: 2px; }
}
</style>
