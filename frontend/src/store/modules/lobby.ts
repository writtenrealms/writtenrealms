import axios from "axios";
import _ from "lodash";

const set_initial_state = () => {
  return {
    world: null,
    chars: null,
    leaderboards: [],
    leaderboardError: false,
    leaderboardsLoading: false,
    requestedWorldId: null,

    create_character: false,

  };
};

const actions = {
  initial_fetch: async ({ commit, dispatch, state }, world_id) => {
    commit('reset_state');
    commit('requested_world_set', String(world_id));

    const worldFetchPromise = axios.get(
      `/lobby/worlds/${world_id}/`
    );

    const userCharsPromise = axios.get(
      `/lobby/worlds/${world_id}/chars/?page_size=100`
    );

    const leaderboardPromise = dispatch('fetch_leaderboards', world_id);

    const [world_resp, user_chars_resp] = await Promise.all([
      worldFetchPromise,
      userCharsPromise,
      leaderboardPromise,
    ]);

    if (state.requestedWorldId !== String(world_id)) return;
    commit('set_chars', user_chars_resp.data.results);
    commit('set_world', world_resp.data);
  },
  fetch_leaderboards: async ({ commit, state }, world_id) => {
    commit('leaderboards_loading', true);
    try {
      const response = await axios.get(`/lobby/worlds/${world_id}/leaderboards/`);
      if (state.requestedWorldId !== String(world_id)) return;
      commit('set_leaderboards', response.data.panels);
    } catch {
      if (state.requestedWorldId === String(world_id)) commit('leaderboards_error', true);
    } finally {
      if (state.requestedWorldId === String(world_id)) commit('leaderboards_loading', false);
    }
  },
};

const mutations = {
  reset_state: (state) => {
    Object.assign(state, set_initial_state());
  },
  create_character_set: (state, true_or_false) => {
    state.create_character = true_or_false;
  },
  set_chars: (state, chars) => {
    state.chars = chars;
  },
  char_create: (state, char) => {
    state.chars.splice(0, 0, char);
  },
  set_world: (state, world) => {
    state.world = world;
  },
  requested_world_set: (state, id) => { state.requestedWorldId = id; },
  leaderboards_loading: (state, loading) => { state.leaderboardsLoading = loading; },
  leaderboards_error: (state, error) => { state.leaderboardError = error; },
  set_leaderboards: (state, panels) => {
    state.leaderboards = panels;
    state.leaderboardError = false;
  },
};

export default {
  namespaced: true,
  state: set_initial_state(),
  actions,
  mutations: mutations
};
