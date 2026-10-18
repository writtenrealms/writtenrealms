<template>
  <div class="markdown-content" v-html="html"></div>
</template>

<script lang="ts" setup>
import { computed } from 'vue';
import { renderMarkdown } from '@/core/markdown';

const props = defineProps<{ source: string }>();
const html = computed(() => renderMarkdown(props.source));
</script>

<style lang="scss">
@import '@/styles/colors.scss';
@import '@/styles/fonts.scss';

.markdown-content {
  @include font-text-light;
  font-size: 16px;
  line-height: 1.8;
  overflow-wrap: anywhere;

  > :first-child { margin-top: 0; }
  > :last-child { margin-bottom: 0; }

  h1, h2, h3, h4, h5, h6 {
    @include font-title-regular;
    color: $color-secondary;
    line-height: 1.3;
    margin: 1.8em 0 0.7em;
    text-transform: none;
  }
  h1 { font-size: 32px; }
  h2 { font-size: 26px; border-top: 1px solid $color-background-light-border; padding-top: 24px; }
  > h2:first-child { border-top: 0; padding-top: 0; }
  h3 { font-size: 22px; }
  h4, h5, h6 { font-size: 18px; }
  p, ul, ol, blockquote, pre, table { margin: 1em 0; }
  ul, ol { padding-left: 1.5em; }
  ul { list-style: disc; }
  ol { list-style: decimal; }
  li + li { margin-top: 0.4em; }
  li > ul, li > ol { margin: 0.4em 0; }
  strong { font-weight: 400; }
  a { text-decoration: underline; text-underline-offset: 0.18em; }
  blockquote {
    border-left: 3px solid $color-form-border;
    padding: 0 1em;
    color: $color-text-hex-70;
  }
  code { @include font-mono; background: $color-form-background; padding: 0.15em 0.35em; border-radius: 3px; }
  pre {
    @include font-mono;
    font-size: 14px;
    font-weight: 400;
    line-height: 1.75;
    background: $color-form-background;
    border: 1px solid $color-background-light-border;
    border-radius: 6px;
    padding: 18px 20px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    tab-size: 4;
  }
  pre code {
    display: block;
    font: inherit;
    white-space: inherit;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: none;
  }
  table { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; }
  th, td { padding: 8px 14px; border: 1px solid $color-form-border; text-align: left; }
  th { @include font-text-regular; background: $color-background-light; }
  img { display: block; max-width: 100%; height: auto; margin: 1em 0; }
  hr { border: 0; border-top: 1px solid $color-form-border; margin: 2em 0; }
}
</style>
