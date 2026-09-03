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
    <div v-else-if="abilityHelp" class="ability-help">
      <div>{{ message.text }}</div>
    </div>
    <div v-else>
      <div class="cmd-group basic-commands">
        <div class="group-title">Basic Commands</div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('quit')">quit</div>
          <div class="cmd" @click="cmdHelp('rest')">rest</div>
          <div class="cmd" @click="cmdHelp('stand')">stand</div>
          <div class="cmd" @click="cmdHelp('alias')">alias</div>
          <div class="cmd" @click="cmdHelp('roll')">roll</div>
          <div class="cmd" @click="cmdHelp('learn')">learn</div>
          <div class="cmd" @click="cmdHelp('unlearn')">unlearn</div>
          <div class="cmd" @click="cmdHelp('hotkey')">hotkey</div>
          <div class="cmd" @click="cmdHelp('title')" v-if="canSetTitle">title</div>
        </div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('follow')">follow</div>
          <div class="cmd" @click="cmdHelp('unfollow')">unfollow</div>
          <div class="cmd" @click="cmdHelp('group')">group</div>
          <div class="cmd" @click="cmdHelp('ungroup')">ungroup</div>
          <div class="cmd" @click="cmdHelp('socials')">socials</div>
        </div>
      </div>

      <div class="cmd-group combat-commands">
        <div class="group-title">Combat Commands</div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('kill')">kill</div>
          <div class="cmd" @click="cmdHelp('disengage')">disengage</div>
          <div class="cmd" @click="cmdHelp('flee')">flee</div>
          <div class="cmd" @click="cmdHelp('duel')">duel</div>
          <div class="cmd" @click="cmdHelp('focus')">focus</div>
          <div class="cmd" @click="cmdHelp('ambush')">ambush</div>
          <div class="cmd" @click="cmdHelp('assist')">assist</div>
          <div class="cmd" @click="cmdHelp('taunt')">taunt</div>
        </div>
      </div>

      <div class="cmd-group information-commands">
        <div class="group-title">Information</div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('look')">look</div>
          <div class="cmd" @click="cmdHelp('inspect')">inspect</div>
          <div class="cmd" @click="cmdHelp('stats')">stats</div>
          <div class="cmd" @click="cmdHelp('inventory')">inventory</div>
          <div class="cmd" @click="cmdHelp('who')">who</div>
          <div class="cmd" @click="cmdHelp('where')">where</div>
          <div class="cmd" @click="cmdHelp('whois')">whois</div>
        </div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('factions')">factions</div>
          <div class="cmd" @click="cmdHelp('scan')">scan</div>
          <div class="cmd" @click="cmdHelp('track')">track</div>
          <div class="cmd" @click="cmdHelp('currencies')">currencies</div>
        </div>
      </div>

      <div class="cmd-group movement-commands">
        <div class="group-title">Movement</div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('north')">north</div>
          <div class="cmd" @click="cmdHelp('east')">east</div>
          <div class="cmd" @click="cmdHelp('west')">west</div>
          <div class="cmd" @click="cmdHelp('south')">south</div>
          <div class="cmd" @click="cmdHelp('up')">up</div>
          <div class="cmd" @click="cmdHelp('down')">down</div>
          <div class="cmd" @click="cmdHelp('exits')">exits</div>
        </div>
      </div>

      <div class="cmd-group door-commands">
        <div class="group-title">Doors</div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('open')">open</div>
          <div class="cmd" @click="cmdHelp('close')">close</div>
          <div class="cmd" @click="cmdHelp('lock')">lock</div>
          <div class="cmd" @click="cmdHelp('unlock')">unlock</div>
        </div>
      </div>

      <div class="cmd-group communication-commands">
        <div class="group-title">Communication</div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('say')">say</div>
          <div class="cmd" @click="cmdHelp('chat')">chat</div>
          <div class="cmd" @click="cmdHelp('gossip')">gossip</div>
          <div class="cmd" @click="cmdHelp('emote')">emote</div>
          <div class="cmd" @click="cmdHelp('tell')">tell</div>
          <div class="cmd" @click="cmdHelp('reply')">reply</div>
          <div class="cmd" @click="cmdHelp('yell')">yell</div>
          <div class="cmd" @click="cmdHelp('mute')">mute</div>
        </div>
      </div>

      <div class="cmd-group clan-commands">
        <div class="group-title">Clan Commands</div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('cc')">cc</div>
          <div class="cmd" @click="cmdHelp('cjoin')">cjoin</div>
          <div class="cmd" @click="cmdHelp('cquit')">cquit</div>
          <div class="cmd" @click="cmdHelp('cregister')">cregister</div>
          <div class="cmd" @click="cmdHelp('cpassword')">cpassword</div>
          <div class="cmd" @click="cmdHelp('cmembers')">cmembers</div>
          <div class="cmd" @click="cmdHelp('cpromote')">cpromote</div>
          <div class="cmd" @click="cmdHelp('ckick')">ckick</div>
        </div>
      </div>

      <div class="cmd-group item-commands">
        <div class="group-title">Item Manipulation</div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('get')">get</div>
          <div class="cmd" @click="cmdHelp('put')">put</div>
          <div class="cmd" @click="cmdHelp('drop')">drop</div>
          <div class="cmd" @click="cmdHelp('give')">give</div>
          <div class="cmd" @click="cmdHelp('wield')">wield</div>
          <div class="cmd" @click="cmdHelp('wear')">wear</div>
          <div class="cmd" @click="cmdHelp('remove')">remove</div>
        </div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('label')">label</div>
          <div class="cmd" @click="cmdHelp('compare')">compare</div>
          <div class="cmd" @click="cmdHelp('eat')">eat</div>
        </div>
      </div>

      <div class="cmd-group quest-commands">
        <div class="group-title">Quest Commands</div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('quest')">quest</div>
        </div>
      </div>

      <div class="cmd-group quest-mobs">
        <div class="group-title">Mob Commands</div>
        <div class="cmds">
          <div class="cmd" @click="cmdHelp('list')">list</div>
          <div class="cmd" @click="cmdHelp('offer')">offer</div>
          <div class="cmd" @click="cmdHelp('buy')">buy</div>
          <div class="cmd" @click="cmdHelp('sell')">sell</div>
          <div class="cmd" @click="cmdHelp('order')">order</div>

        </div>
      </div>

      <template v-if="isBuilder">
        <div class="cmd-group">
          <div class="group-title">Builder Commands</div>
          <div class="cmds">
            <div class="cmd" @click="cmdHelp('/load')">/load</div>
            <div class="cmd" @click="cmdHelp('reset')">reset</div>
            <div class="cmd" @click="cmdHelp('geta')">/geta</div>
            <div class="cmd" @click="cmdHelp('seta')">/seta</div>
            <div class="cmd" @click="cmdHelp('/regen')">/regen</div>
            <div class="cmd" @click="cmdHelp('/purge')">/purge</div>
            <div class="cmd" @click="cmdHelp('masspurge')">/masspurge</div>
            <div class="cmd" @click="cmdHelp('/cmd')">/cmd</div>
          </div>
          <div class="cmds">
            <div class="cmd" @click="cmdHelp('/invisible')">/invisible</div>
            <div class="cmd" @click="cmdHelp('/repop')">/repop</div>
            <div class="cmd" @click="cmdHelp('ping')">/ping</div>
            <div class="cmd" @click="cmdHelp('/edit')">/edit</div>
            <div class="cmd" @click="cmdHelp('jump')">/jump</div>
            <div class="cmd" @click="cmdHelp('transfer')">/transfer</div>
            <div class="cmd" @click="cmdHelp('find')">/find</div>
          </div>
          <div class="cmds">
            <div class="cmd" @click="cmdHelp('/at')">/at</div>
            <div class="cmd" @click="cmdHelp('/damage')">/damage</div>
            <div class="cmd" @click="cmdHelp('/echo')">/echo</div>
            <div class="cmd" @click="cmdHelp('/wecho')">/wecho</div>
            <div class="cmd" @click="cmdHelp('/zecho')">/zecho</div>
            <div class="cmd" @click="cmdHelp('/send')">/send</div>
            <div class="cmd" @click="cmdHelp('/sendexcept')">/sendexcept</div>
          </div>
          <div class="cmds">
            <div class="cmd" @click="cmdHelp('/state')">/state</div>
            <div class="cmd" @click="cmdHelp('/kill')">/kill</div>
            <div class="cmd" @click="cmdHelp('/kick')">/kick</div>

          </div>
          <div class="cmds">
            <div class="cmd" @click="cmdHelp('/open')">/open</div>
            <div class="cmd" @click="cmdHelp('/close')">/close</div>
            <div class="cmd" @click="cmdHelp('/lock')">/lock</div>
            <div class="cmd" @click="cmdHelp('/unlock')">/unlock</div>
            <div class="cmd" @click="cmdHelp('/chat')">/chat</div>
            <div class="cmd" @click="cmdHelp('/take')">/take</div>
            <div class="cmd" @click="cmdHelp('/kill')">/kill</div>
            <div class="cmd" @click="cmdHelp('/ban')">/ban</div>
            <div class="cmd" @click="cmdHelp('/mute')">/mute</div>
            <div class="cmd" @click="cmdHelp('/nochat')">/nochat</div>
          </div>
          <div class="cmds">
          </div>
        </div>
      </template>
    </div>
  </div>
</template>


<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";

const store = useStore();
const props = defineProps<{ message: any }>();

const isBuilder = computed(() => store.state.game.player.is_builder);
const canSetTitle = computed(() => store.state.game.world.players_can_set_title);
const topicHelp = computed(() => {
  const data = props.message?.data || {};
  return data.command || null;
});
const abilityHelp = computed(() => {
  const data = props.message?.data || {};
  return data.ability || null;
});
const cmdHelp = (cmd) => {
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
      //justify-content: space-between;
      .cmd:not(:last-child) {
        margin-right: 8px;
      }

      .cmd {
        //@include font-text-regular;
        //text-decoration: underline;
        border-bottom: 1px dotted #888;
        cursor: pointer;
      }
    }
  }
}
</style>
