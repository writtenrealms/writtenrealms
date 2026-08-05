import { defineConfig, type DefaultTheme } from "vitepress";

const builderSidebar: DefaultTheme.SidebarItem[] = [
  { text: "Builder home", link: "/builders/" },
  { text: "YAML manifests", link: "/builders/yaml-manifests" },
  {
    text: "World structure",
    collapsed: false,
    items: [
      { text: "World config", link: "/builders/world-config-builder-guide" },
      { text: "World publishing", link: "/builders/world-publishing" },
      { text: "Rooms and doors", link: "/builders/room-builder-guide" },
      { text: "Instances", link: "/builders/instance-builder-guide" },
      { text: "Spawn plans", link: "/builders/spawn-plan-builder-guide" },
    ],
  },
  {
    text: "Definitions and economy",
    collapsed: true,
    items: [
      { text: "Items and item bundles", link: "/builders/item-definition-builder-guide" },
      { text: "Currencies", link: "/builders/currency-builder-guide" },
      { text: "Merchants", link: "/builders/merchant-builder-guide" },
      { text: "Crafting", link: "/builders/crafting-builder-guide" },
    ],
  },
  {
    text: "Mobs and combat",
    collapsed: true,
    items: [
      { text: "Mob definitions", link: "/builders/mob-definition-builder-guide" },
      { text: "Mob traits", link: "/builders/mob-trait-builder-guide" },
      { text: "Factions", link: "/builders/faction-builder-guide" },
      { text: "Attributes", link: "/builders/attributes-builder-guide" },
      { text: "Leveling", link: "/builders/leveling-builder-guide" },
      { text: "Abilities", link: "/builders/ability-builder-guide" },
      { text: "Attack routines", link: "/builders/attack-routine-builder-guide" },
      { text: "Combat formulas", link: "/builders/combat-formula-builder-guide" },
    ],
  },
  {
    text: "Scripting and interactions",
    collapsed: true,
    items: [
      { text: "Conditions", link: "/builders/condition-builder-guide" },
      { text: "State", link: "/builders/state-builder-guide" },
      { text: "Triggers", link: "/builders/trigger-builder-guide" },
      { text: "Room actions", link: "/builders/room-actions" },
      { text: "Builder slash commands", link: "/builders/builder-command-reference" },
      { text: "Socials", link: "/builders/social-builder-guide" },
    ],
  },
  {
    text: "Quests",
    collapsed: true,
    items: [
      { text: "Quest builder", link: "/builders/quest-builder-guide" },
      { text: "Quest reference", link: "/builders/quest-reference" },
    ],
  },
  {
    text: "Examples",
    collapsed: true,
    items: [
      { text: "WR1-like classed demo", link: "/builders/wr1-like-demo-world-guide" },
      { text: "WR1-like classless demo", link: "/builders/wr1-like-classless-demo-world-guide" },
    ],
  },
];

const playerSidebar: DefaultTheme.SidebarItem[] = [
  { text: "Player home", link: "/players/" },
  { text: "Map", link: "/players/map" },
  { text: "Doors and keys", link: "/players/doors-and-keys" },
  { text: "Scripted interactions", link: "/players/scripted-interactions" },
  { text: "Combat", link: "/players/combat" },
  { text: "Duels", link: "/players/duels" },
  { text: "Currencies", link: "/players/currencies" },
  { text: "Crafting", link: "/players/crafting-player-guide" },
  { text: "Socials", link: "/players/socials" },
];

export default defineConfig({
  srcDir: "guides",
  lang: "en-US",
  title: "Written Realms Guides",
  titleTemplate: ":title | Written Realms Guides",
  description: "Builder and player guides for Written Realms.",
  cleanUrls: true,
  lastUpdated: true,
  appearance: "dark",
  ignoreDeadLinks: false,
  head: [
    ["meta", { name: "theme-color", content: "#191a1c" }],
  ],
  markdown: {
    lineNumbers: true,
    config(markdown) {
      const renderInlineCode = markdown.renderer.rules.code_inline;
      markdown.renderer.rules.code_inline = (tokens, index, options, env, renderer) => {
        const rendered = renderInlineCode
          ? renderInlineCode(tokens, index, options, env, renderer)
          : renderer.renderToken(tokens, index, options);
        return rendered.replace(/^<code>/, "<code v-pre>");
      };
    },
  },
  themeConfig: {
    nav: [
      { text: "Home", link: "/" },
      { text: "Builder Guides", link: "/builders/" },
      { text: "Player Guides", link: "/players/" },
      { text: "Written Realms", link: "https://writtenrealms.com" },
    ],
    sidebar: {
      "/builders/": builderSidebar,
      "/players/": playerSidebar,
    },
    search: {
      provider: "local",
    },
    outline: {
      level: [2, 3],
      label: "On this page",
    },
    socialLinks: [
      { icon: "github", link: "https://github.com/writtenrealms/writtenrealms" },
    ],
    editLink: {
      pattern: "https://github.com/writtenrealms/writtenrealms/edit/main/docs/guides/:path",
      text: "Edit this guide on GitHub",
    },
    lastUpdated: {
      text: "Last updated",
      formatOptions: {
        dateStyle: "medium",
      },
    },
    docFooter: {
      prev: "Previous guide",
      next: "Next guide",
    },
    externalLinkIcon: true,
    footer: {
      message: "Guides for Written Realms builders and players.",
      copyright: "Written Realms",
    },
  },
});
