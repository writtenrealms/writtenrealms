import axios from "axios";
import { Module } from "vuex";
import { ActionMethods } from "@/types/vuex-types";

import { API_BASE } from "@/config";

const LOGIN_REQUEST_ENDPOINT = `${API_BASE}auth/email/request/`;
const LOGIN_CONFIRM_ENDPOINT = `${API_BASE}auth/email/confirm/`;
const REFRESH_ENDPOINT = `${API_BASE}auth/refresh/`;

interface State {
  token: string;
  refreshToken: string;
  status: string;
  user: Record<string, any>;
}

const state: State = {
  token: localStorage.getItem("jwtToken") || "",
  refreshToken: localStorage.getItem("refreshToken") || "",
  status: "",
  user: JSON.parse(localStorage.getItem("user") || "{}"),
};

const actions = {
  login: async ({ commit, dispatch }: ActionMethods, creds: any) => {
    try {
      await axios.post(LOGIN_REQUEST_ENDPOINT, creds);
      commit(
        "ui/notification_set",
        "Check your email for a login link.",
        { root: true }
      );
    } catch (error: any) {
      if (error.response?.data?.non_field_errors) {
        commit(
          "ui/notification_set_error",
          error.response.data.non_field_errors[0],
          {
            root: true
          }
        );
      } else {
        dispatch("ui/process_error_response", error, { root: true });
      }
      localStorage.removeItem("jwtToken");
      localStorage.removeItem("refreshToken");
    }
  },

  logout: async ({ commit }: ActionMethods) => {
    commit("auth_clear");
  },

  confirm_login_link: async ({ commit }: ActionMethods, token: string) => {
    const resp = await axios.post(LOGIN_CONFIRM_ENDPOINT, {
      token: token
    });
    commit("auth_set_tokens", {
      access: resp.data.access || resp.data.token,
      refresh: resp.data.refresh
    });
    commit("user_set", resp.data.user);
    return resp;
  },

  renew: async ({ commit, state }: ActionMethods) => {
    if (!state.refreshToken) {
      return;
    }
    const resp = await axios.post(REFRESH_ENDPOINT, {
      refresh: state.refreshToken
    });
    commit("auth_set_tokens", {
      access: resp.data.access || resp.data.token,
      refresh: state.refreshToken
    });
  },

  signup: async ({ commit }: ActionMethods, payload: any) => {
    try {
      const resp = await axios.post("/auth/signup/", payload);
      commit(
        "ui/notification_set",
        "Check your email for a login link.",
        { root: true }
      );
      return resp;
    } catch (error: any) {
      if (
        error.response.data.email &&
        error.response.data.email[0] === "This field must be unique."
      ) {
        commit("ui/notification_set_error", "Email is already registered.", {
          root: true
        });
      } else if (error.response.data.password) {
        commit("ui/notification_set_error", error.response.data.password[0], {
          root: true
        });
      }
      return Promise.reject(error);
    }
  },

  google_login: async ({ commit }: ActionMethods, credential: string) => {
    try {
      const resp = await axios.post("/auth/google/login/", { credential: credential });
      commit("auth_set_tokens", {
        access: resp.data.access || resp.data.token,
        refresh: resp.data.refresh
      });
      commit("user_set", resp.data.user);
      return { success: true, resp: resp }
    } catch (error: any) {
      commit('ui/notification_set_error', 'Error logging in.')
      return { success: false, error: error}
    }
  },

  save: async ({ commit }: ActionMethods, payload: any) => {
    try {
      const resp = await axios.post("auth/save/", payload);
      commit("auth_set_tokens", {
        access: resp.data.access || resp.data.token,
        refresh: resp.data.refresh
      });
      commit("user_set", resp.data.user);
      commit(
        "ui/notification_set",
        "Account saved. Check your email for a login link.",
        { root: true});
      return { success: true, data: resp.data };
    } catch (error: any) {
      if (
        error.response.data.email &&
        error.response.data.email[0] === "This field must be unique."
      ) {
        commit("ui/notification_set_error", "Wrong password.", {
          root: true
        });
      } else if (error.response.data.password) {
        commit("ui/notification_set_error", error.response.data.password[0], {
          root: true
        });
      }
      return { success: false, error: error };
    }
  },

  google_save: async ({ commit }: ActionMethods, credential: string) => {
    try {
      const resp = await axios.post("/auth/google/save/", { credential: credential });
      commit("auth_set_tokens", {
        access: resp.data.access || resp.data.token,
        refresh: resp.data.refresh
      });
      commit("user_set", resp.data.user);
      return { success: true, resp: resp };
    } catch (error: any) {
      commit('ui/notification_set_error', 'Error saving user.')
      return { success: false, error: error}
    };
  },

  account_save: async ({ commit }: ActionMethods, payload: any) => {
    const resp = await axios.put("/user/", payload);
    commit("user_set", resp.data);
  },

  forgotpassword: async ({}, payload: any) => {
    const resp = await axios.post("/auth/email/request/", payload);
    if (resp.status === 201) {
      return true;
    }
  },

  resendemailconfirmation: async ({ commit, state }: ActionMethods, payload: any) => {
    try {
      const email = payload?.email || state.user?.email;
      await axios.post("/auth/email/request/", { email });
      commit("ui/notification_set", "Login link sent.", {
        root: true
      });
    } catch (error: any) {
      if (error.response.data.non_field_errors) {
        commit(
          "ui/notification_set_error",
          error.response.data.non_field_errors[0],
          {
            root: true
          }
        );
      }
    }
  },

  accept_code_of_conduct: async ({ commit }: ActionMethods) => {
    const resp = await axios.post("/auth/acceptcodeofconduct/", {});
    commit("user_set", resp.data);
  },

};

const mutations = {
  auth_set(state: State, token: string) {
      state.status = "authenticated";
      state.token = token;
      localStorage.setItem("jwtToken", token);
  },

  auth_set_tokens(state: State, tokens: { access: string; refresh?: string }) {
      state.status = "authenticated";
      state.token = tokens.access;
      if (tokens.refresh) {
        state.refreshToken = tokens.refresh;
        localStorage.setItem("refreshToken", tokens.refresh);
      }
      localStorage.setItem("jwtToken", tokens.access);
  },

  auth_clear: (state: State) => {
    state.status = "unauthenticated";
    state.token = "";
    state.refreshToken = "";
    localStorage.removeItem("jwtToken");
    localStorage.removeItem("refreshToken");
    localStorage.removeItem("user");
  },

  user_set: (state: State, user: any) => {
    if (user.name === null) {
      user.name = "";
    }
    state.user = user;
    localStorage.user = JSON.stringify(user);
  },

};

const authModule: Module<State, any> = {
  namespaced: true,
  state,
  getters: {},
  actions,
  mutations,
}

export default authModule;
