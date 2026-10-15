import axios from "axios";
import store from "@/store";
import router from "@/router";
import { API_BASE } from "@/config";
import { accessNotice } from "@/core/accessNotice";

export default function interceptorSetup() {

  axios.interceptors.request.use(function (config) {
    const token = store.state["auth"].token;
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });

  axios.interceptors.response.use(
    function(response) {
      return response;
    },
    function (error) {
      if (axios.isCancel(error)) {
        // Handle the cancellation error specifically
        return Promise.reject(error);
      }

      if (error.response.status == 401) {
        store.commit("auth/auth_clear");
        // router.push({ name: "login" });
      } else if (error.response.status == 403) {
        if (["signup", "login"].includes(String(router.currentRoute.value.name)) && accessNotice(error)) {
          // Auth forms keep admission notices beside the fields without a toast.
          return Promise.reject(error);
        }
        if (router.currentRoute.value.name != "game") {
          const detail = error.response.data?.detail;
          store.commit(
            "ui/notification_set_error",
            typeof detail === "string" && detail.trim()
              ? detail
              : "You are not authorized to perform this action"
          );
        }
      } else if (error.response.status == 400) {
        const errorData = error.response.data;
        // The creation form shows this policy notice beside its controls.
        if (errorData.code === 'character_limit_reached') return Promise.reject(error);
        let errorMsg = "Error";
        if (errorData.non_field_errors && errorData.non_field_errors.length) {
          errorMsg += ": " + errorData.non_field_errors[0];
        } else if (Array.isArray(errorData)) {
          errorMsg += ": " + errorData[0];
        } else {
          const error_keys = Object.keys(errorData);
          if (error_keys.length) {
            errorMsg += ":"
            for (let i = 0; i < error_keys.length; i++) {
              let fieldName = error_keys[i];
              let fieldError = errorData[fieldName][0];
              errorMsg += "\n" + fieldName + " - " + fieldError;
            }
          } else {
            errorMsg += ".";
          }
        }
        store.commit("ui/notification_set_error", errorMsg);
      }
      return Promise.reject(error);
    }
  );

  axios.defaults.baseURL = API_BASE;
}
