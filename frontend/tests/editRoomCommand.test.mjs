import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const helperSource = await readFile(
  new URL("../src/core/editRoomCommand.ts", import.meta.url),
  "utf8",
);
const gameStoreSource = await readFile(
  new URL("../src/store/modules/game.ts", import.meta.url),
  "utf8",
);
const helpSource = await readFile(
  new URL("../src/components/game/console/Help.vue", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(helperSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const editRoomCommand = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

const makeTab = () => {
  const replacements = [];
  return {
    closed: false,
    closeCalls: 0,
    opener: { retained: true },
    replacements,
    close() {
      this.closeCalls += 1;
      this.closed = true;
    },
    location: {
      replace(href) {
        replacements.push(href);
      },
    },
  };
};

const makeBrowserWindow = (...results) => {
  const calls = [];
  return {
    calls,
    open(...args) {
      calls.push(args);
      return results.shift() ?? null;
    },
  };
};

test.afterEach(() => {
  editRoomCommand.cancelAllEditRoomTabs();
});

test("only a direct /edit command reserves an editor tab", () => {
  for (const command of [
    "/edit",
    "  /edit  ",
    "/EDIT",
    "/EdIt room@48",
    "/edit 1",
  ]) {
    assert.equal(
      editRoomCommand.isDirectEditRoomCommand(command),
      true,
      command,
    );
  }

  for (const command of [
    "/editor",
    "/editing room@48",
    "edit",
    "help /edit",
    "look; /edit",
    "",
    null,
    48,
  ]) {
    assert.equal(
      editRoomCommand.isDirectEditRoomCommand(command),
      false,
      String(command),
    );
  }
});

test("preparing /edit synchronously reserves a detached blank tab", () => {
  const tab = makeTab();
  const browserWindow = makeBrowserWindow(tab);

  assert.equal(
    editRoomCommand.prepareEditRoomTab(
      "request-1",
      "  /EDIT room@48  ",
      browserWindow,
    ),
    true,
  );
  assert.deepEqual(browserWindow.calls, [["about:blank", "_blank"]]);
  assert.equal(tab.opener, null);
  assert.equal(tab.closed, false);
});

test("a correlated result navigates only its reserved tab", () => {
  const firstTab = makeTab();
  const secondTab = makeTab();
  const browserWindow = makeBrowserWindow(firstTab, secondTab);
  editRoomCommand.prepareEditRoomTab(
    "request-1",
    "/edit room@47",
    browserWindow,
  );
  editRoomCommand.prepareEditRoomTab(
    "request-2",
    "/edit room@48",
    browserWindow,
  );

  assert.equal(
    editRoomCommand.openResolvedEditRoomTab(
      "request-2",
      "/build/worlds/23/rooms/48",
      browserWindow,
    ),
    true,
  );
  assert.deepEqual(firstTab.replacements, []);
  assert.deepEqual(
    secondTab.replacements,
    ["/build/worlds/23/rooms/48"],
  );
  assert.equal(browserWindow.calls.length, 2);
  assert.equal(editRoomCommand.cancelEditRoomTab("request-2"), false);
  assert.equal(editRoomCommand.cancelEditRoomTab("request-1"), true);
  assert.equal(firstTab.closed, true);
});

test("cancelling a pending edit closes it and consumes the correlation", () => {
  const tab = makeTab();
  const browserWindow = makeBrowserWindow(tab);
  editRoomCommand.prepareEditRoomTab("request-1", "/edit", browserWindow);

  assert.equal(editRoomCommand.cancelEditRoomTab("request-1"), true);
  assert.equal(tab.closeCalls, 1);
  assert.equal(tab.closed, true);
  assert.equal(editRoomCommand.cancelEditRoomTab("request-1"), false);
  assert.equal(editRoomCommand.cancelEditRoomTab(null), false);
});

test("resolved edits fall back to a detached direct popup without a reservation", () => {
  const fallbackTab = makeTab();
  const browserWindow = makeBrowserWindow(fallbackTab);

  assert.equal(
    editRoomCommand.openResolvedEditRoomTab(
      "unreserved-request",
      "/build/worlds/23/rooms/48",
      browserWindow,
    ),
    true,
  );
  assert.deepEqual(
    browserWindow.calls,
    [["/build/worlds/23/rooms/48", "_blank"]],
  );
  assert.equal(fallbackTab.opener, null);
  assert.deepEqual(fallbackTab.replacements, []);
});

test("a closed reservation also uses the direct-popup fallback", () => {
  const reservedTab = makeTab();
  const fallbackTab = makeTab();
  const browserWindow = makeBrowserWindow(reservedTab, fallbackTab);
  editRoomCommand.prepareEditRoomTab("request-1", "/edit", browserWindow);
  reservedTab.closed = true;

  assert.equal(
    editRoomCommand.openResolvedEditRoomTab(
      "request-1",
      "/build/worlds/23/rooms/48",
      browserWindow,
    ),
    true,
  );
  assert.deepEqual(browserWindow.calls, [
    ["about:blank", "_blank"],
    ["/build/worlds/23/rooms/48", "_blank"],
  ]);
  assert.equal(fallbackTab.opener, null);
  assert.deepEqual(reservedTab.replacements, []);
});

test("popup blocking is reported for reservation and fallback opens", () => {
  const browserWindow = makeBrowserWindow(null, null);

  assert.equal(
    editRoomCommand.prepareEditRoomTab("request-1", "/edit", browserWindow),
    false,
  );
  assert.equal(
    editRoomCommand.openResolvedEditRoomTab(
      "request-1",
      "/build/worlds/23/rooms/48",
      browserWindow,
    ),
    false,
  );
  assert.deepEqual(browserWindow.calls, [
    ["about:blank", "_blank"],
    ["/build/worlds/23/rooms/48", "_blank"],
  ]);
});

test("cancelling all edits closes every live reservation", () => {
  const firstTab = makeTab();
  const secondTab = makeTab();
  const browserWindow = makeBrowserWindow(firstTab, secondTab);
  editRoomCommand.prepareEditRoomTab("request-1", "/edit", browserWindow);
  editRoomCommand.prepareEditRoomTab("request-2", "/edit room@48", browserWindow);

  editRoomCommand.cancelAllEditRoomTabs();

  assert.equal(firstTab.closed, true);
  assert.equal(secondTab.closed, true);
  assert.equal(editRoomCommand.cancelEditRoomTab("request-1"), false);
  assert.equal(editRoomCommand.cancelEditRoomTab("request-2"), false);
});

test("game command integration prepares before sending and opens the canonical route", () => {
  const cmdStart = gameStoreSource.indexOf(
    "cmd: async ({ dispatch, state, commit }, cmd) =>",
  );
  const cmdEnd = gameStoreSource.indexOf("cmd_structured:", cmdStart);
  const cmdSource = gameStoreSource.slice(cmdStart, cmdEnd);
  const prepareIndex = cmdSource.indexOf("prepareEditRoomTab(requestId, cmd)");
  const sendIndex = cmdSource.indexOf(
    'await dispatch("sendWSMessage", wireMessage)',
  );

  assert.ok(cmdStart !== -1);
  assert.ok(cmdEnd > cmdStart);
  assert.ok(prepareIndex !== -1);
  assert.ok(sendIndex > prepareIndex);
  assert.match(
    cmdSource,
    /if \(state\.player\?\.is_builder\)\s*\{\s*prepareEditRoomTab\(requestId, cmd\)/,
  );

  const successStart = gameStoreSource.indexOf(
    'if (message_data.type === "cmd./edit.success")',
  );
  const errorStart = gameStoreSource.indexOf(
    '} else if (message_data.type === "cmd./edit.error")',
    successStart,
  );
  const successSource = gameStoreSource.slice(successStart, errorStart);
  assert.ok(successStart !== -1);
  assert.ok(errorStart > successStart);
  assert.match(successSource, /builderRoomIndexRoute\(/);
  assert.match(successSource, /router\.resolve\(route\)\.href/);
  assert.match(successSource, /openResolvedEditRoomTab\(requestId, href\)/);
});

test("game command integration closes reservations on failures and state reset", () => {
  assert.match(
    gameStoreSource,
    /message_data\.type === "cmd\.\/edit\.error"\)\s*\{\s*cancelEditRoomTab\(requestId\)/,
  );
  assert.match(
    gameStoreSource,
    /code === "command_delivery_unconfirmed"[^]*cancelEditRoomTab\(requestId\)/,
  );
  assert.match(
    gameStoreSource,
    /const sent = await dispatch\("sendWSMessage", wireMessage\);\s*if \(!sent\)\s*\{\s*cancelEditRoomTab\(requestId\)/,
  );
  assert.match(
    gameStoreSource,
    /reset_state: \(state\) => \{\s*clearCommandReceiptTimers\(\);\s*cancelAllEditRoomTabs\(\)/,
  );
  assert.match(
    gameStoreSource,
    /const onerror = \(error\) => \{[^}]*cancelAllEditRoomTabs\(\)/,
  );
  assert.match(
    gameStoreSource,
    /const onclose = \(\) => \{\s*cancelAllEditRoomTabs\(\)/,
  );
});

test("builder help lists the /edit command and requests its help topic", () => {
  const builderHelpStart = helpSource.indexOf('<template v-if="isBuilder">');
  const builderHelpEnd = helpSource.indexOf("</template>", builderHelpStart);
  const builderHelpSource = helpSource.slice(builderHelpStart, builderHelpEnd);

  assert.ok(builderHelpStart !== -1);
  assert.ok(builderHelpEnd > builderHelpStart);
  assert.match(
    builderHelpSource,
    /class="cmd" @click="cmdHelp\('\/edit'\)"\s*>\/edit<\/div>/,
  );
  assert.match(helpSource, /store\.dispatch\("game\/cmd", `help \$\{cmd\}`\)/);
});
