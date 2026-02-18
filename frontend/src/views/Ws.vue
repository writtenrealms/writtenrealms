<template>
  <div class="ws-view">
    <h1>FastAPI WebSocket</h1>
    <div class="ws-card">
      <div class="ws-row">
        <span class="label">Status</span>
        <span :class="['value', statusClass]">{{ status }}</span>
      </div>
      <div class="ws-row" v-if="userId">
        <span class="label">User ID</span>
        <span class="value">{{ userId }}</span>
      </div>
      <div class="ws-error" v-if="error">
        {{ error }}
      </div>
      <div class="ws-actions">
        <button class="button" @click="reconnect">Reconnect</button>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useStore } from "vuex";
import { FORGE_WS_URI } from "@/config";

const store = useStore();
const status = ref("disconnected");
const userId = ref<string | null>(null);
const error = ref("");
let socket: WebSocket | null = null;

const token = computed(() => store.state.auth.token);
const statusClass = computed(() => `status-${status.value}`);

const connect = () => {
  error.value = "";
  userId.value = null;

  if (!token.value) {
    status.value = "missing_token";
    error.value = "Missing auth token. Log in first.";
    return;
  }

  if (!FORGE_WS_URI) {
    status.value = "missing_uri";
    error.value = "Missing VITE_FORGE_WS_URI config.";
    return;
  }

  status.value = "connecting";
  const wsUrl = `${FORGE_WS_URI}?token=${encodeURIComponent(token.value)}`;
  socket = new WebSocket(wsUrl);

  socket.onopen = () => {
    status.value = "connected";
  };

  socket.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload?.user_id !== undefined) {
        userId.value = String(payload.user_id);
      }
    } catch (err) {
      error.value = "Received non-JSON message.";
    }
  };

  socket.onclose = () => {
    status.value = "disconnected";
  };

  socket.onerror = () => {
    error.value = "WebSocket error.";
  };
};

const reconnect = () => {
  if (socket) {
    socket.close();
    socket = null;
  }
  connect();
};

onMounted(connect);
onBeforeUnmount(() => {
  if (socket) {
    socket.close();
  }
});
</script>

<style lang="scss">
.ws-view {
  padding: 40px 24px;
}

.ws-card {
  max-width: 520px;
  background: #0f1b17;
  border: 1px solid #1f3b34;
  border-radius: 12px;
  padding: 20px;
  color: #d8f0e8;
}

.ws-row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 10px;
}

.label {
  font-weight: 600;
  color: #9cc7b9;
}

.value {
  font-family: "IBM Plex Mono", "Courier New", monospace;
}

.status-connected {
  color: #4cd3a5;
}

.status-connecting {
  color: #f3d17a;
}

.status-disconnected,
.status-missing_token,
.status-missing_uri {
  color: #f08d8d;
}

.ws-error {
  margin: 12px 0;
  color: #f08d8d;
}

.ws-actions {
  margin-top: 12px;
}

.button {
  background: #1e6f5c;
  color: #e9fff7;
  border: none;
  border-radius: 8px;
  padding: 8px 14px;
  cursor: pointer;
}

.button:hover {
  background: #238069;
}
</style>
