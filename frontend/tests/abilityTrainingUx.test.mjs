import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const readSource = (path) => readFile(new URL(path, import.meta.url), "utf8");

const [
  providerSource,
  trainerServiceSource,
  trainerListSource,
  trainerDetailsSource,
  roomConfigSource,
  roomConfigServiceSource,
  routerSource,
  worldConfigSource,
  editWorldSource,
  exportSource,
  builderFrameSource,
  consoleSource,
  trainingListSource,
  trainerLearningSource,
  lookRoomSource,
  charActionsSource,
  lookupCharSource,
  gameStoreSource,
] = await Promise.all([
  readSource("../src/core/trainingProviders.ts"),
  readSource("../src/services/trainers.ts"),
  readSource("../src/views/builder/world/TrainerProfileList.vue"),
  readSource("../src/views/builder/world/TrainerProfileDetails.vue"),
  readSource("../src/views/builder/room/Config.vue"),
  readSource("../src/services/roomConfig.ts"),
  readSource("../src/router/index.ts"),
  readSource("../src/views/builder/world/Config.vue"),
  readSource("../src/views/builder/world/EditWorld.vue"),
  readSource("../src/views/builder/world/ExportWorld.vue"),
  readSource("../src/components/builder/BuilderFrame.vue"),
  readSource("../src/components/game/console/Console.vue"),
  readSource("../src/components/game/console/AbilityTrainingList.vue"),
  readSource("../src/core/trainerLearning.ts"),
  readSource("../src/components/game/console/LookRoom.vue"),
  readSource("../src/core/charActions.ts"),
  readSource("../src/components/game/lookup/LookupChar.vue"),
  readSource("../src/store/modules/game.ts"),
]);

const compiledProviderSource = ts.transpileModule(providerSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const providers = await import(
  `data:text/javascript;base64,${Buffer.from(compiledProviderSource).toString("base64")}`
);

const compiledCharActionsSource = ts.transpileModule(charActionsSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const charActions = await import(
  `data:text/javascript;base64,${Buffer.from(compiledCharActionsSource).toString("base64")}`
);

const compiledTrainerLearningSource = ts.transpileModule(trainerLearningSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const trainerLearning = await import(
  `data:text/javascript;base64,${Buffer.from(compiledTrainerLearningSource).toString("base64")}`
);

test("training providers use typed room and live mob identity", () => {
  const roomProvider = {
    type: "room",
    id: 42,
    key: "room.42",
    name: "Hall of Forms",
    profile: { id: 7, key: "trainerprofile.7", slug: "forms", name: "Forms" },
  };
  assert.deepEqual(
    providers.getRoomTrainingProvider({ training_provider: roomProvider }),
    roomProvider,
  );
  assert.equal(
    providers.trainingProviderIsAvailableInRoom(
      { type: "room", key: "room.42" },
      { training_provider: roomProvider },
    ),
    true,
  );
  assert.equal(
    providers.trainingProviderIsAvailableInRoom(
      { type: "room", key: "room.42" },
      { training_provider: { ...roomProvider, key: "room.99", id: 99 } },
    ),
    false,
  );
  assert.equal(
    providers.trainingProviderIsAvailableInRoom(
      { type: "mob", key: "mob.9" },
      { chars: [{ id: 9, key: "mob.9", name: "Mentor", is_trainer: true }] },
    ),
    true,
  );
  assert.equal(
    providers.trainingProviderIsAvailableInRoom(
      { type: "mob", key: "mob.9" },
      { chars: [] },
    ),
    false,
  );
});

test("direct training rooms expose deduplicated learn and unlearn actions", () => {
  assert.deepEqual(
    providers.roomActionsForTrainingProvider({
      training_provider: { type: "room", key: "room.42" },
      actions: ["craft", "LEARN", "learn"],
    }),
    ["craft", "LEARN", "unlearn"],
  );
  assert.deepEqual(
    providers.roomActionsForTrainingProvider({ actions: ["craft"] }),
    ["craft"],
  );
  assert.deepEqual(
    providers.roomActionsForTrainingProvider({
      training_provider: { type: "mob", key: "mob.9" },
      chars: [{ key: "mob.9", is_trainer: true }],
      actions: ["learn"],
    }),
    ["learn", "unlearn"],
  );
  assert.deepEqual(
    providers.roomActionsForTrainingProvider({
      training_provider: { type: "mob", key: "mob.9" },
      chars: [],
      actions: ["craft", "learn", "unlearn"],
    }),
    ["craft"],
  );
});

test("trainer NPCs expose bare learn and unlearn interactions", () => {
  const actions = charActions.buildCharActions(
    { char_type: "mob", is_trainer: true },
    {},
    {},
  );
  assert.equal(actions.learn, true);
  assert.equal(actions.unlearn, true);
  const authoredActions = charActions.buildCharActions(
    {
      char_type: "mob",
      is_trainer: true,
      actions: [{ action: "learn", label: "STUDY", command: "learn", exact: true }],
    },
    {},
    {},
  );
  assert.deepEqual(
    authoredActions.learn,
    { action: "learn", label: "STUDY", command: "learn", exact: true },
  );
  assert.match(lookupCharSource, /\{ action: "learn", label: "LEARN", exact: true \}/);
  assert.match(lookupCharSource, /\{ action: "unlearn", label: "UNLEARN", exact: true \}/);
  assert.match(lookupCharSource, /if \(action\?\.exact \|\| rawAction\.includes\(" "\)\)/);
});

test("NPC training is discoverable in room actions without a room profile", () => {
  for (const name of ["Othryades", "Demeas the Hoplomachos"]) {
    const room = {
      training_provider: null,
      chars: [{ key: "mob.9", name, is_trainer: true }],
      actions: ["inspect tablets"],
    };
    assert.deepEqual(
      providers.roomActionsForTrainingProvider(room),
      ["inspect tablets", "learn", "unlearn"],
    );
    assert.deepEqual(room.actions, ["inspect tablets"]);
  }
});

test("room training follows NPC arrival, departure, and server availability", () => {
  const room = { chars: [], actions: ["craft"] };
  assert.deepEqual(providers.roomActionsForTrainingProvider(room), ["craft"]);

  room.chars.push({ key: "mob.9", is_trainer: true, health: 30 });
  assert.deepEqual(
    providers.roomActionsForTrainingProvider(room),
    ["craft", "learn", "unlearn"],
  );

  // A defeated trainer can remain available under the 'present' policy.
  room.chars[0].health = 0;
  assert.deepEqual(
    providers.roomActionsForTrainingProvider(room),
    ["craft", "learn", "unlearn"],
  );
  room.chars[0].is_trainer = false;
  assert.deepEqual(providers.roomActionsForTrainingProvider(room), ["craft"]);

  room.chars[0].is_trainer = true;
  room.chars = [];
  assert.deepEqual(providers.roomActionsForTrainingProvider(room), ["craft"]);

  assert.match(lookRoomSource, /store\.state\.game\.room\?\.id === room\.value\.id/);
  assert.match(lookRoomSource, /roomActionsForTrainingProvider\(\s*roomTrainingContext\.value/);
});

test("multiple room and NPC trainers share one pair of training actions", () => {
  const chars = [
    { key: "mob.9", is_trainer: true },
    { key: "mob.10", is_trainer: true },
  ];
  for (const training_provider of [
    null,
    { type: "room", key: "room.42" },
    { type: "mob", key: "mob.departed" },
  ]) {
    const sourceActions = ["list", " LEARN ", "learn", "offer", "UNLEARN"];
    assert.deepEqual(
      providers.roomActionsForTrainingProvider({ training_provider, chars }, sourceActions),
      ["list", "LEARN", "offer", "UNLEARN"],
    );
    assert.deepEqual(sourceActions, ["list", " LEARN ", "learn", "offer", "UNLEARN"]);
  }
  assert.deepEqual(
    providers.roomActionsForTrainingProvider({
      training_provider: { type: "room", key: "room.42" },
      chars: [],
    }),
    ["learn", "unlearn"],
  );
});

test("combined merchant trainers keep every service action discoverable", () => {
  const actions = charActions.buildCharActions(
    { char_type: "mob", is_trainer: true, is_merchant: true },
    {},
    {},
  );
  assert.equal(actions.learn, true);
  assert.equal(actions.unlearn, true);
  assert.equal(actions.list, true);
  assert.equal(actions.offer, true);
  assert.match(
    lookupCharSource,
    /hasTrainingActions && hasMerchantActions \? 5 : 3/,
  );
  assert.ok(
    lookupCharSource.indexOf('{ action: "list"')
      < lookupCharSource.indexOf('{ action: "talk"'),
  );
  assert.ok(
    lookupCharSource.indexOf('{ action: "offer"')
      < lookupCharSource.indexOf('{ action: "talk"'),
  );
});

test("builder trainer profiles use typed list and shared manifest detail flows", () => {
  assert.match(trainerServiceSource, /\/trainerprofiles\//);
  assert.match(trainerServiceSource, /Promise<TrainerProfileDetail>/);
  assert.match(trainerListSource, /title="Trainer Profiles"/);
  assert.match(trainerListSource, /ability_count/);
  assert.match(trainerListSource, /read-only in this instance/);
  assert.doesNotMatch(trainerListSource, /<ElementList\s+v-else/);
  assert.match(trainerDetailsSource, /<ManifestResourceDetails/);
  assert.match(trainerDetailsSource, /expected-kind="trainerprofile"/);
  assert.match(trainerDetailsSource, /response-field="trainer_profile"/);
  assert.match(trainerDetailsSource, /detail-id-param="trainer_profile_id"/);
  assert.match(trainerServiceSource, /TrainerProfileLearningPolicy/);
  assert.match(trainerServiceSource, /max_known\?: number \| "uncapped" \| null/);
  assert.match(trainerDetailsSource, /Learning policy/);
  assert.match(trainerDetailsSource, /Each learner may select up to/);
  assert.match(trainerDetailsSource, /Eligibility conditions/);
  assert.match(routerSource, /builder_trainer_profile_list/);
  assert.match(routerSource, /builder_trainer_profile_details/);
  assert.match(worldConfigSource, /title: "Trainer Profiles"/);
  assert.match(builderFrameSource, /'builder_trainer_profile_details'/);
});

test("World Edit and Export understand trainer profile manifests", () => {
  assert.match(editWorldSource, /trainerprofile: "trainer_profile"/);
  assert.match(editWorldSource, /trainerprofile: "Trainer profile"/);
  assert.match(editWorldSource, /kind: trainerprofile/);
  assert.match(editWorldSource, /learning:\n    conditions: \{\}\n    max_known: uncapped/);
  assert.match(editWorldSource, /prefill === "new-trainer-profile"/);
  assert.match(editWorldSource, /name: "builder_trainer_profile_details"/);
  assert.match(exportSource, /summary\.trainer_profiles/);
});

test("Room Config can search, clear, open, and save a trainer profile", () => {
  assert.match(roomConfigSource, /<h4>ABILITY TRAINING<\/h4>/);
  assert.match(roomConfigSource, /:endpoint="trainerProfileEndpoint"/);
  assert.match(roomConfigSource, /@click="onClearTrainerProfile"/);
  assert.match(roomConfigSource, /@click="onSaveTrainerProfile"/);
  assert.match(roomConfigSource, /builder_trainer_profile_details/);
  assert.match(roomConfigServiceSource, /\{ trainer_profile: trainerProfileId \}/);
  assert.match(roomConfigServiceSource, /trainerProfileId: number \| null/);
  assert.match(roomConfigServiceSource, /can_edit_training\?: boolean/);
  assert.match(roomConfigSource, /const canEditTraining = computed/);
  assert.match(roomConfigSource, /v-if="canEditTraining"/);
  assert.match(roomConfigSource, /require a\s+senior builder/);
  assert.match(roomConfigSource, /if \(!canEditTraining\.value/);
});

test("learn and unlearn lists render rich, guarded exact commands and cap state", () => {
  assert.match(consoleSource, /"cmd\.ability\.learn\.list": AbilityTrainingList/);
  assert.match(consoleSource, /"cmd\.ability\.unlearn\.list": AbilityTrainingList/);
  assert.match(trainingListSource, /ability\?\.learn_command/);
  assert.match(trainingListSource, /ability\?\.unlearn_command/);
  assert.match(trainingListSource, /:value="ability\.number"/);
  assert.match(trainingListSource, /Select an ability, or use:/);
  assert.match(trainingListSource, /trainingProviderIsAvailableInRoom/);
  assert.match(trainingListSource, /store\.state\.game\.messages/);
  assert.match(trainingListSource, /startsWith\("cmd\.ability\.learn\."\)/);
  assert.match(trainingListSource, /startsWith\("cmd\.ability\.unlearn\."\)/);
  assert.match(trainingListSource, /knownSlugs/);
  assert.match(trainingListSource, /\? knownSlugs\.value\.has/);
  assert.match(trainingListSource, /: !knownSlugs\.value\.has/);
  assert.match(trainingListSource, /You know the maximum of/);
  assert.match(trainingListSource, /ability limit is uncapped/);
  assert.match(trainingListSource, /trainerLearningChoiceIsAvailable/);
  assert.match(trainingListSource, /trainerLearningStatusText/);
  assert.match(trainerLearningSource, /Unlearn one to choose another/);
  assert.match(trainingListSource, /:disabled="!canSelect\(ability\)"/);
  assert.match(consoleSource, /trainer_learning_limit/);
  assert.match(consoleSource, /trainer_learning_denied/);
  assert.match(trainingListSource, /Only the first/);
  assert.match(lookRoomSource, /roomActionsForTrainingProvider/);
  const disabledAbilityRule = trainingListSource.match(
    /\.ability-name:disabled\s*\{([^}]*)\}/,
  )?.[1] || "";
  assert.match(disabledAbilityRule, /color:\s*inherit/);
  assert.doesNotMatch(disabledAbilityRule, /opacity\s*:/);
});

test("profile learning status normalizes embedded and top-level quota payloads", () => {
  const trainer = {
    profile: {
      id: 12,
      key: "trainerprofile.12",
      slug: "hoplite-cross-training",
      name: "Hoplite Cross-Training",
    },
    learning: {
      status: "limit_reached",
      eligible: false,
      max_known: 2,
      known: 2,
      remaining: 0,
    },
  };
  const embedded = trainerLearning.trainerLearningStatusForAbility(
    { slug: "bash", trainer },
    {},
  );
  assert.equal(embedded.profileName, "Hoplite Cross-Training");
  assert.equal(embedded.maxKnown, 2);
  assert.equal(embedded.known, 2);
  assert.equal(embedded.remaining, 0);
  assert.equal(trainerLearning.trainerLearningChoiceIsAvailable(embedded), false);
  assert.equal(
    trainerLearning.trainerLearningStatusText(embedded),
    "Hoplite Cross-Training — 2 of 2 selected. Unlearn one to choose another.",
  );

  const topLevel = trainerLearning.trainerLearningStatusForAbility(
    {
      slug: "guard",
      trainer: {
        profile: { key: "trainerprofile.12", slug: "hoplite-cross-training" },
      },
    },
    {
      training_limits: [{
        profile_key: "trainerprofile.12",
        profile_name: "Hoplite Cross-Training",
        status: "available",
        eligible: true,
        max_known: 2,
        known: 1,
        remaining: 1,
      }],
    },
  );
  assert.equal(topLevel.status, "available");
  assert.equal(topLevel.remaining, 1);
  assert.equal(trainerLearning.trainerLearningChoiceIsAvailable(topLevel), true);
});

test("denied learning copy keeps the unlearn escape path explicit", () => {
  const denied = trainerLearning.normalizeTrainerLearningStatus({
    profile_name: "Hoplite Training",
    status: "denied",
    eligible: false,
    max_known: 6,
    known: 2,
    remaining: 4,
  });
  assert.ok(denied);
  assert.equal(
    trainerLearning.trainerLearningStatusText(denied),
    "Hoplite Training — This training is not available to you.",
  );
  assert.equal(
    trainerLearning.trainerLearningStatusText(denied, { unlearn: true }),
    "Hoplite Training — Learning is unavailable, but you can still unlearn known abilities here.",
  );
  assert.match(trainingListSource, /learningStatusText\(group\.learning\)/);
  assert.match(trainingListSource, /unlearn: isUnlearn\.value/);
});

test("profile learning policy errors retain trainer identity and denial reason", () => {
  const statuses = trainerLearning.trainerLearningStatusesForMessage({
    trainer: {
      profile: {
        id: 31,
        slug: "restricted-training",
        name: "Restricted Training",
      },
    },
    learning: {
      status: "denied",
      eligible: false,
      max_known: 2,
      known: 0,
      remaining: 2,
      reason: "Only Tidecallers may study here.",
    },
  });
  assert.equal(statuses.length, 1);
  assert.equal(statuses[0].profileId, 31);
  assert.equal(statuses[0].eligible, false);
  assert.equal(
    trainerLearning.trainerLearningStatusText(statuses[0]),
    "Restricted Training — Only Tidecallers may study here.",
  );
});

test("ability mutations immediately merge authoritative actor state", () => {
  assert.match(gameStoreSource, /"cmd\.ability\.learn\.success"/);
  assert.match(gameStoreSource, /"cmd\.ability\.unlearn\.success"/);
  assert.match(gameStoreSource, /"cmd\.ability\.hotkey\.success"/);
  assert.match(gameStoreSource, /"player\.abilities\.update"/);
  assert.match(gameStoreSource, /const abilityActor = message_data\.data\?\.actor/);
  assert.match(gameStoreSource, /commit\("player_set", abilityActor\)/);
});
