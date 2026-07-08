import axios from "axios";

const initial_state = () => {
  return {};
};

const zone_actions = {
  move_zone: async ({ rootState, commit }, data) => {
    const resp = await axios.post(
      `/builder/worlds/${rootState.builder.world.id}/zones/${rootState.builder.zone.id}/move/`,
      data
    );
    commit("builder/map_add", resp.data, { root: true });
  },
};

export default {
  namespaced: true,
  state: initial_state(),
  actions: {
    ...zone_actions,
  },
  mutations: {},
};
