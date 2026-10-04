import axios from "axios";

const baseURL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
export const apiBaseUrl = baseURL;

export const api = axios.create({ baseURL });

const TOKEN_KEY = "reo_access_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // localStorage unavailable (private mode etc.) — session simply won't persist across reloads
  }
}

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (error) => {
    // A 401 means the session is gone — send the user to sign in again. Not for the sign-in
    // request itself, though: there a 401 just means "wrong password", and the login page has to
    // stay put to show that (and the 429 "too many attempts" message) instead of reloading blank.
    const isLoginRequest = String(error?.config?.url ?? "").includes("/auth/login");
    if (error?.response?.status === 401 && !isLoginRequest) {
      setToken(null);
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

export interface LoginResponse {
  access_token: string;
  tenant_id: string;
  roles: string[];
  display_name: string;
}

export async function login(tenant_slug: string, email: string, password: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/auth/login", { tenant_slug, email, password });
  return data;
}

export async function me() {
  const { data } = await api.get("/auth/me");
  return data;
}
