<template>
  <div class="styleguide">
    <h1>H1 HEADER lowercase</h1>
    <h2>h2 HEADER lowercase</h2>
    <h3>H3 HEADER lowercase</h3>

    <p class="font-text-regular">Font-Text Regular</p>
    <p class="font-text-light">Font-Text Light</p>
    <p class="font-title-regular">Font-Title Regular</p>
    <p class="font-title-light">Font-Title Light</p>
    <p class="color-red">Color-Red</p>
    <p class="color-green">Color-Green</p>
    <p class="color-green-chat">Color-Green-Chat</p>
    <p class="color-blue-chat">Color-Blue-Chat</p>

    <div>
      <a href="#" @click.prevent="dummyPrevent">link</a>
    </div>

    <div>
      <button>PLAIN BUTTON</button>
    </div>

    <div>
      <button class="btn-small">BTN-SMALL</button>
      <button class="btn-small button-red">BTN-SMALL RED</button>
    </div>

    <div>
      <button class="btn-medium">BTN-MEDIUM</button>
    </div>

    <div>
      <button class="btn-large">BTN-LARGE</button>
    </div>

    <div>
      <button class="btn-add">BTN-ADD</button>
    </div>

    <div>
      <button class="btn-thin">BTN-THIN</button>
      <button class="btn-thin">BUTTON #2</button>
    </div>

    <div class="panel-wrapper" style="width: 300px">
      <div class="panel panel-shadow">
        <div>.panel .panel-shadow</div>
      </div>
    </div>

    <div class="styleguide-section">
      <h2>Link Grid</h2>
      <h3>With Description</h3>
      <div class="link-grid">
        <a class="link-grid-item" href="#" @click.prevent="dummyPrevent">
          <span class="link-grid-title">World Admin</span>
          <span class="link-grid-description">Connected players, maintenance mode, and spawned worlds.</span>
        </a>
        <a class="link-grid-item" href="#" @click.prevent="dummyPrevent">
          <span class="link-grid-title">Abilities</span>
          <span class="link-grid-description">Manifest-backed combat and utility commands.</span>
        </a>
        <a class="link-grid-item" href="#" @click.prevent="dummyPrevent">
          <span class="link-grid-title">Instances</span>
          <span class="link-grid-description">Private instance contexts created from a world.</span>
        </a>
      </div>

      <h3>Title Only</h3>
      <div class="link-grid link-grid-title-only">
        <a class="link-grid-item" href="#" @click.prevent="dummyPrevent">
          <span class="link-grid-title">Lobby</span>
        </a>
        <a class="link-grid-item" href="#" @click.prevent="dummyPrevent">
          <span class="link-grid-title">Zones</span>
        </a>
        <a class="link-grid-item" href="#" @click.prevent="dummyPrevent">
          <span class="link-grid-title">Admin</span>
        </a>
      </div>
    </div>

    <div class="styleguide-section">
      <h2>Data Display</h2>

      <h3>Key Value Table</h3>
      <table class="data-table key-value-table">
        <tbody>
          <tr>
            <th scope="row">Starting Room</th>
            <td>Corinthian Countryside</td>
          </tr>
          <tr>
            <th scope="row">PvP Mode</th>
            <td>PvP Zones</td>
          </tr>
          <tr>
            <th scope="row">Death Route</th>
            <td>top_faction</td>
          </tr>
        </tbody>
      </table>

      <h3>Record Table</h3>
      <div class="data-table-scroll styleguide-narrow-table">
        <table class="data-table record-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Stat</th>
              <th>Type</th>
              <th>Base</th>
              <th>Constant</th>
              <th>Cap</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="rating in ratingRows" :key="rating.name">
              <th scope="row">{{ rating.name }}</th>
              <td>{{ rating.stat }}</td>
              <td>{{ rating.type }}</td>
              <td>{{ rating.base }}</td>
              <td>{{ rating.constant }}</td>
              <td>{{ rating.cap }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h3>Manifest Value</h3>
      <ManifestValue :value="manifestExample" />
    </div>
  </div>
</template>

<style scoped lang='scss'>
@import "@/styles/fonts.scss";
@import "@/styles/colors.scss";

.styleguide {
  padding: 20px;

  > div {
    margin: 5px 0;
  }

  .styleguide-section {
    margin-top: 30px;
    max-width: 900px;

    h2,
    h3 {
      margin-bottom: 10px;
    }

    h3 {
      margin-top: 20px;
    }
  }

  .styleguide-narrow-table {
    max-width: 520px;
  }
}
</style>

<script lang='ts' setup>
import ManifestValue from "@/components/builder/world/ManifestValue.vue";

const dummyPrevent = () => {};

const ratingRows = [
  {
    name: "Dodge",
    stat: "dodge",
    type: "mitigation_curve",
    base: 0.02,
    constant: 60,
    cap: 0.75,
  },
  {
    name: "Crit",
    stat: "crit",
    type: "linear_rating",
    base: 0.02,
    constant: 120,
    cap: 1,
  },
  {
    name: "Resilience",
    stat: "resilience",
    type: "mitigation_curve",
    base: 0,
    constant: 120,
    cap: 0.75,
  },
];

const manifestExample = {
  combat: {
    version: 1,
    default_attack_profile: "basic_physical",
    variance: {
      enabled: true,
      percent: 12.5,
    },
    ratings: {
      dodge: {
        stat: "dodge",
        type: "mitigation_curve",
        base: 0.02,
        constant: 60,
        cap: 0.75,
      },
      crit: {
        stat: "crit",
        type: "linear_rating",
        base: 0.02,
        constant: 120,
        cap: 1,
      },
      resilience: {
        stat: "resilience",
        type: "mitigation_curve",
        base: 0,
        constant: 120,
        cap: 0.75,
      },
    },
  },
  player_rules: {
    can_select_faction: true,
    auto_equip: true,
    globals_enabled: true,
    decay_glory: false,
  },
};
</script>
