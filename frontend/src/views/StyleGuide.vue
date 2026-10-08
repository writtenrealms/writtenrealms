<template>
  <div class="styleguide">
    <header class="sg-intro">
      <h1>Style Guide</h1>
      <p class="color-text-60">
        Shared styles from <span class="sg-code">src/styles</span>, most used first. Components import tokens with
        <span class="sg-code">@import "@/styles/colors.scss"</span> (and <span class="sg-code">fonts</span>,
        <span class="sg-code">layout</span>); those files only define variables and mixins, so importing them adds no CSS.
      </p>
      <nav class="sg-nav">
        <a v-for="section in sections" :key="section.id" :href="`#${section.id}`">{{ section.title }}</a>
      </nav>
    </header>

    <!-- Text color -->
    <section id="text-color" class="sg-section">
      <h2>Text color</h2>
      <p class="sg-note">Utility classes for de-emphasis and accents. Prefer these over one-off colors.</p>
      <div class="sg-rows">
        <div v-for="utility in textUtilities" :key="utility.className" class="sg-row">
          <span class="sg-code">.{{ utility.className }}</span>
          <span :class="utility.className">{{ utility.sample }}</span>
        </div>
      </div>
    </section>

    <!-- Buttons -->
    <section id="buttons" class="sg-section">
      <h2>Buttons</h2>
      <p class="sg-note">All button labels are uppercase. Use one primary action per view.</p>
      <div class="sg-rows">
        <div class="sg-row">
          <span class="sg-code">.btn-small</span>
          <div class="sg-inline">
            <button class="btn-small">PLAY</button>
            <button class="btn-small button-gray">CANCEL</button>
            <button class="btn-small button-red">DELETE</button>
            <button class="btn-small" disabled>DISABLED</button>
          </div>
        </div>
        <div class="sg-row">
          <span class="sg-code">.btn-thin</span>
          <div class="sg-inline">
            <button class="btn-thin">SHOW ALL</button>
            <button class="btn-thin">RETRY</button>
          </div>
        </div>
        <div class="sg-row">
          <span class="sg-code">.btn-medium</span>
          <div class="sg-inline">
            <button class="btn-medium">CONTINUE AS ONYX</button>
            <button class="btn-medium button-gray">NEW CHARACTER</button>
          </div>
        </div>
        <div class="sg-row">
          <span class="sg-code">.icon-button</span>
          <div class="sg-inline">
            <button class="icon-button" aria-label="Edit"><Pencil aria-hidden="true" /></button>
            <button class="icon-button" aria-label="Share"><Share aria-hidden="true" /></button>
            <button class="icon-button" aria-label="More actions"><Ellipsis aria-hidden="true" /></button>
          </div>
        </div>
        <div class="sg-row">
          <span class="sg-code">.btn-add</span>
          <div class="sg-demo-narrow"><button class="btn-add">CREATE NEW CHARACTER</button></div>
        </div>
        <div class="sg-row">
          <span class="sg-code">.btn-large</span>
          <div class="sg-demo-narrow"><button class="btn-large">ENTER WORLD</button></div>
        </div>
      </div>
      <p class="sg-note">Modifiers: <span class="sg-code">.button-gray</span> (small, medium) and
        <span class="sg-code">.button-red</span> (small). Keyboard focus shows a gold ring on every control.</p>
    </section>

    <!-- Forms -->
    <section id="forms" class="sg-section">
      <h2>Forms</h2>
      <p class="sg-note">Wrap each field in <span class="sg-code">.form-group</span>. Prefer the
        <span class="sg-code">FormField</span> component, which renders this markup from a schema.</p>
      <div class="sg-form">
        <div class="form-group">
          <label for="sg-name">Name</label>
          <input id="sg-name" type="text" placeholder="Name" value="Onyx" />
        </div>
        <div class="form-group">
          <label for="sg-class">Class</label>
          <select id="sg-class">
            <option>Hoplite</option>
            <option>Tidecaller</option>
            <option>Mystic</option>
          </select>
        </div>
        <div class="form-group">
          <label for="sg-title" class="error">Title <span>(Titles can be at most 40 characters.)</span></label>
          <input id="sg-title" class="error" type="text" value="Hero of Marathon, Breaker of Persian Lines" />
        </div>
        <div class="form-group">
          <label for="sg-description">Description</label>
          <textarea id="sg-description">A broad-shouldered hoplite with a bronze shield.</textarea>
        </div>
        <div class="form-group checkbox">
          <label><input type="checkbox" checked /> Send me the newsletter</label>
        </div>
      </div>
    </section>

    <!-- Typography -->
    <section id="typography" class="sg-section">
      <h2>Typography</h2>
      <p class="sg-note">Titles use Brandon Grotesque (<span class="sg-code">font-title-regular</span> /
        <span class="sg-code">font-title-light</span> mixins). Text uses Roboto Slab, light by default.
        Game output uses Roboto Mono only for briefs and code.</p>
      <div class="sg-rows">
        <div class="sg-row"><span class="sg-code">h1</span><h1>Phalanx</h1></div>
        <div class="sg-row"><span class="sg-code">h2</span><h2>Your Characters</h2></div>
        <div class="sg-row"><span class="sg-code">h3</span><h3>STARTING ROOM</h3></div>
        <div class="sg-row"><span class="sg-code">.section-title</span><div class="section-title sg-flush">World Builder</div></div>
        <div class="sg-row">
          <span class="sg-code">body</span>
          <p class="sg-flush">Dawn of the fifth century BC. The Achaemenid Empire has swallowed the East.</p>
        </div>
        <div class="sg-row">
          <span class="sg-code">.font-text-regular</span>
          <span class="font-text-regular">Onyx, Greek hoplite</span>
        </div>
        <div class="sg-row">
          <span class="sg-code">code</span>
          <span>Type <code>pause</code> or <code>resume</code> at any time.</span>
        </div>
        <div class="sg-row">
          <span class="sg-code">a, a.gray-link</span>
          <span class="sg-inline">
            <a href="#typography" @click.prevent>Primary link</a>
            <a href="#typography" class="gray-link" @click.prevent>Gray link</a>
          </span>
        </div>
      </div>
    </section>

    <!-- Lists -->
    <section id="lists" class="sg-section">
      <h2>Lists</h2>
      <div class="sg-columns">
        <div>
          <h3 class="sg-label">ul.list</h3>
          <ul class="list">
            <li>Bronze spear</li>
            <li>Hoplon shield</li>
            <li>Linen cuirass</li>
          </ul>
        </div>
        <div>
          <h3 class="sg-label">.indented</h3>
          <div>Objectives</div>
          <div class="indented">Reach the Persian outpost</div>
          <div class="indented">Defeat the garrison commander</div>
        </div>
      </div>
    </section>

    <!-- Panels -->
    <section id="panels" class="sg-section">
      <h2>Panels</h2>
      <div class="sg-panels">
        <div class="panel">.panel</div>
        <div class="panel panel-shadow">.panel.panel-shadow</div>
        <div class="panel panel-edit">.panel.panel-edit</div>
        <div class="panel selected">.panel.selected</div>
      </div>
    </section>

    <!-- Icons -->
    <section id="icons" class="sg-section">
      <h2>Icons</h2>
      <p class="sg-note">
        <a href="https://lucide.dev/icons/" target="_blank" rel="noopener">Lucide</a> via
        <span class="sg-code">@lucide/vue</span>. Import each icon by name; they default to 18px with a 1.75 stroke
        (set in <span class="sg-code">App.vue</span>) and take the surrounding text color. Pass
        <span class="sg-code">:size</span> for 16 or 24. Icon-only buttons need an <span class="sg-code">aria-label</span>.
      </p>
      <div class="sg-code-block">import { Pencil } from "@lucide/vue";
&lt;button class="icon-button" aria-label="Edit this world"&gt;&lt;Pencil aria-hidden="true" /&gt;&lt;/button&gt;</div>
      <div class="sg-icons">
        <div v-for="icon in icons" :key="icon.name" class="sg-icon">
          <component :is="icon.component" aria-hidden="true" />
          <span>{{ icon.name }}</span>
        </div>
      </div>
      <div class="sg-inline sg-icon-sizes">
        <span><Swords :size="16" aria-hidden="true" /> 16</span>
        <span><Swords aria-hidden="true" /> 18</span>
        <span><Swords :size="24" aria-hidden="true" /> 24</span>
        <span class="color-secondary"><Coins aria-hidden="true" /> 120 drachmae</span>
      </div>
    </section>

    <!-- Status and feedback -->
    <section id="feedback" class="sg-section">
      <h2>Status and feedback</h2>
      <div class="sg-row sg-flush-row">
        <span class="sg-code">.tag</span>
        <div class="sg-inline">
          <span class="tag">Builder</span>
          <span class="tag tag-secondary">Yours</span>
          <span class="tag tag-green">Approved</span>
          <span class="tag tag-primary">Changes requested</span>
        </div>
      </div>
      <div class="sg-columns">
        <div>
          <h3 class="sg-label">.loading-container .spinner</h3>
          <div class="loading-container sg-loading"><div class="spinner"></div></div>
        </div>
        <div>
          <h3 class="sg-label">.progress-bar</h3>
          <div class="progress-bar">
            <div class="progress-fill" style="width: 62%"></div>
            <div class="progress-label">Channeling 3s</div>
          </div>
        </div>
      </div>
    </section>

    <!-- Navigation -->
    <section id="navigation" class="sg-section">
      <h2>Navigation</h2>
      <h3 class="sg-label">.link-grid</h3>
      <div class="link-grid">
        <a class="link-grid-item" href="#navigation" @click.prevent>
          <span class="link-grid-title">World Admin</span>
          <span class="link-grid-description">Connected players, maintenance mode, and spawned worlds.</span>
        </a>
        <a class="link-grid-item" href="#navigation" @click.prevent>
          <span class="link-grid-title">Abilities</span>
          <span class="link-grid-description">Manifest-backed combat and utility commands.</span>
        </a>
        <a class="link-grid-item" href="#navigation" @click.prevent>
          <span class="link-grid-title">Instances</span>
          <span class="link-grid-description">Private instance contexts created from a world.</span>
        </a>
      </div>
      <h3 class="sg-label">.link-grid.link-grid-title-only</h3>
      <div class="link-grid link-grid-title-only">
        <a class="link-grid-item" href="#navigation" @click.prevent><span class="link-grid-title">Lobby</span></a>
        <a class="link-grid-item" href="#navigation" @click.prevent><span class="link-grid-title">Zones</span></a>
        <a class="link-grid-item" href="#navigation" @click.prevent><span class="link-grid-title">Admin</span></a>
      </div>
    </section>

    <!-- Data -->
    <section id="data" class="sg-section">
      <h2>Data display</h2>
      <div class="sg-columns">
        <div>
          <h3 class="sg-label">.data-table.key-value-table</h3>
          <table class="data-table key-value-table">
            <tbody>
              <tr><th scope="row">Starting Room</th><td>Corinthian Countryside</td></tr>
              <tr><th scope="row">PvP Mode</th><td>PvP Zones</td></tr>
              <tr><th scope="row">Death Route</th><td>top_faction</td></tr>
            </tbody>
          </table>
        </div>
        <div>
          <h3 class="sg-label">.data-table.record-table in .data-table-scroll</h3>
          <div class="data-table-scroll">
            <table class="data-table record-table">
              <thead>
                <tr><th>Name</th><th>Type</th><th>Base</th><th>Cap</th></tr>
              </thead>
              <tbody>
                <tr v-for="rating in ratingRows" :key="rating.name">
                  <th scope="row">{{ rating.name }}</th>
                  <td>{{ rating.type }}</td>
                  <td>{{ rating.base }}</td>
                  <td>{{ rating.cap }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <h3 class="sg-label">ManifestValue component</h3>
      <ManifestValue :value="manifestExample" />
    </section>

    <!-- Color tokens -->
    <section id="colors" class="sg-section">
      <h2>Color tokens</h2>
      <p class="sg-note">SCSS variables from <span class="sg-code">colors.scss</span>.</p>
      <div v-for="group in colorGroups" :key="group.title" class="sg-color-group">
        <h3 class="sg-label">{{ group.title }}</h3>
        <div class="sg-swatches">
          <div v-for="color in group.colors" :key="color.name" class="sg-swatch">
            <span class="sg-chip" :style="{ background: color.value }"></span>
            <span class="sg-code">{{ color.name }}</span>
            <span class="color-text-50">{{ color.value }}</span>
          </div>
        </div>
      </div>
      <h3 class="sg-label">Item quality</h3>
      <div class="sg-inline">
        <span>Bronze spear</span>
        <span class="imbued">Imbued spear</span>
        <span class="enchanted">Enchanted spear</span>
      </div>
    </section>
  </div>
</template>

<script lang="ts" setup>
import {
  Backpack, Check, ChevronDown, Coins, Copy, Ellipsis, ExternalLink, Heart, Info,
  Map as MapIcon, Pencil, Plus, Scroll, Search, Settings, Share, Shield, Skull,
  Sparkles, Swords, Trash, TriangleAlert, User, Users, X,
} from "@lucide/vue";
import ManifestValue from "@/components/builder/world/ManifestValue.vue";

const sections = [
  { id: "text-color", title: "Text color" },
  { id: "buttons", title: "Buttons" },
  { id: "forms", title: "Forms" },
  { id: "typography", title: "Typography" },
  { id: "lists", title: "Lists" },
  { id: "panels", title: "Panels" },
  { id: "icons", title: "Icons" },
  { id: "feedback", title: "Status" },
  { id: "navigation", title: "Navigation" },
  { id: "data", title: "Data" },
  { id: "colors", title: "Color tokens" },
];

const textUtilities = [
  { className: "color-text-50", sample: "Played 21h ago · secondary details" },
  { className: "color-text-60", sample: "Personal bests · Wall-clock time" },
  { className: "color-secondary", sample: "Section labels and highlights" },
  { className: "color-text-70", sample: "Supporting text that should stay readable" },
  { className: "color-primary", sample: "Actions and links" },
  { className: "color-text-red", sample: "Errors and destructive outcomes" },
  { className: "color-text-30", sample: "Disabled or placeholder text" },
  { className: "color-text", sample: "Default text, to reset an inherited color" },
];

const icons = [
  { name: "Pencil", component: Pencil },
  { name: "Share", component: Share },
  { name: "Ellipsis", component: Ellipsis },
  { name: "Plus", component: Plus },
  { name: "Trash", component: Trash },
  { name: "X", component: X },
  { name: "Check", component: Check },
  { name: "Search", component: Search },
  { name: "Copy", component: Copy },
  { name: "ExternalLink", component: ExternalLink },
  { name: "ChevronDown", component: ChevronDown },
  { name: "Settings", component: Settings },
  { name: "Info", component: Info },
  { name: "TriangleAlert", component: TriangleAlert },
  { name: "User", component: User },
  { name: "Users", component: Users },
  { name: "Swords", component: Swords },
  { name: "Shield", component: Shield },
  { name: "Heart", component: Heart },
  { name: "Skull", component: Skull },
  { name: "Sparkles", component: Sparkles },
  { name: "Backpack", component: Backpack },
  { name: "Coins", component: Coins },
  { name: "Scroll", component: Scroll },
  { name: "Map", component: MapIcon },
];

const colorGroups = [
  {
    title: "Brand",
    colors: [
      { name: "$color-primary", value: "#d77617" },
      { name: "$color-primary-70", value: "#9e5b18" },
      { name: "$color-secondary", value: "#f5c983" },
      { name: "$color-tertiary", value: "#279084" },
    ],
  },
  {
    title: "Text",
    colors: [
      { name: "$color-text", value: "#ebebeb" },
      { name: "$color-text-gray", value: "#c5c5c5" },
      { name: "$color-text-hex-70", value: "#b3b3b3" },
      { name: "$color-text-hex-60", value: "#999999" },
      { name: "$color-text-hex-50", value: "#808080" },
      { name: "$color-text-hex-30", value: "#555555" },
    ],
  },
  {
    title: "Surfaces and borders",
    colors: [
      { name: "$color-background-black", value: "#121315" },
      { name: "$color-background-header", value: "#131217" },
      { name: "$color-background", value: "#191a1c" },
      { name: "$color-background-light", value: "#212121" },
      { name: "$color-background-light-border", value: "#2b2b2b" },
      { name: "$color-background-very-light", value: "#333333" },
      { name: "$color-background-border", value: "#444444" },
      { name: "$color-form-background", value: "#111111" },
      { name: "$color-form-border", value: "#424242" },
    ],
  },
  {
    title: "Status",
    colors: [
      { name: "$color-green", value: "#279084" },
      { name: "$color-green-dark", value: "#16514a" },
      { name: "$color-red", value: "#c13434" },
      { name: "$color-red-70", value: "#8b2b2e" },
    ],
  },
  {
    title: "Chat",
    colors: [
      { name: "$color-blue-chat", value: "#4798c4" },
      { name: "$color-green-chat", value: "#84c9aa" },
      { name: "$color-private-chat", value: "#e69292" },
    ],
  },
];

const ratingRows = [
  { name: "Dodge", type: "mitigation_curve", base: 0.02, cap: 0.75 },
  { name: "Crit", type: "linear_rating", base: 0.02, cap: 1 },
  { name: "Resilience", type: "mitigation_curve", base: 0, cap: 0.75 },
];

const manifestExample = {
  combat: {
    version: 1,
    default_attack_profile: "basic_physical",
    variance: { enabled: true, percent: 12.5 },
  },
  player_rules: {
    can_select_faction: true,
    auto_equip: true,
    decay_glory: false,
  },
};
</script>

<style scoped lang="scss">
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
@import "@/styles/layout.scss";

.styleguide {
  width: 100%;
  max-width: $site-max-width;
  margin: 0 auto;
  padding: 40px 30px 80px;

  @media ($mobile-site) {
    padding: 24px 15px 60px;
  }
}

.sg-intro {
  display: grid;
  gap: 12px;
  margin-bottom: 8px;

  p {
    margin: 0;
    max-width: 75ch;
  }
}

.sg-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  padding: 12px 0;
  border-bottom: 1px solid $color-background-light-border;

  a {
    @include font-title-regular;
    font-size: 13px;
    text-transform: uppercase;
    color: $color-text-hex-60;

    &:hover {
      color: $color-primary;
    }
  }
}

.sg-section {
  padding: 36px 0;
  border-bottom: 1px solid $color-background-light-border;
  scroll-margin-top: 20px;

  > h2 {
    margin-bottom: 6px;
  }
}

.sg-note {
  color: $color-text-hex-60;
  max-width: 75ch;
  margin: 0 0 16px;
}

// Class name labels
.sg-code {
  @include font-mono;
  font-size: 12px;
  color: $color-secondary;
  overflow-wrap: anywhere;
}

.sg-label {
  margin: 20px 0 12px;
  text-transform: none;
}

.sg-rows {
  display: grid;
}

.sg-row {
  display: grid;
  grid-template-columns: minmax(0, 180px) minmax(0, 1fr);
  gap: 20px;
  align-items: center;
  padding: 12px 0;
  border-top: 1px solid $color-background-light-border;

  @media ($mobile-site) {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
}

.sg-inline {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
}

.sg-flush {
  margin: 0;
}

.sg-flush-row {
  border-top: 0;
  margin-bottom: 8px;
}

.sg-demo-narrow {
  max-width: 360px;
}

.sg-columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr));
  gap: 0 40px;
}

.sg-form {
  max-width: 420px;
}

.sg-panels {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(200px, 100%), 1fr));
  gap: 16px;

  .panel {
    @include font-mono;
    font-size: 12px;
    padding: 16px;
  }
}

.sg-code-block {
  @include font-mono;
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: $color-text-hex-70;
  background: $color-background-black;
  border: 1px solid $color-background-light-border;
  padding: 10px 14px;
  margin-bottom: 20px;
}

.sg-icons {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 8px;
}

.sg-icon {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid $color-background-light-border;
  font-size: 12px;
  color: $color-text-hex-70;
  overflow-wrap: anywhere;
}

.sg-icon-sizes {
  margin-top: 20px;

  span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}

.sg-loading {
  height: auto;
  transform: none;
  align-items: flex-start;
}

.sg-color-group + .sg-color-group {
  margin-top: 4px;
}

.sg-swatches {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(250px, 100%), 1fr));
  gap: 10px 20px;
}

.sg-swatch {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  font-size: 12px;
}

.sg-chip {
  width: 28px;
  height: 28px;
  border: 1px solid $color-background-border;
  border-radius: 2px;
}
</style>
