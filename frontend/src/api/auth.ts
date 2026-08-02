import { apiFetch } from "./client";
import type { AuthResponse, User } from "./types";

export const login = (email: string, password: string): Promise<AuthResponse> =>
  apiFetch<AuthResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });

export const register = (
  email: string,
  password: string,
  displayName: string,
): Promise<AuthResponse> =>
  apiFetch<AuthResponse>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, display_name: displayName }),
  });

export const getCurrentUser = (): Promise<User> => apiFetch<User>("/api/v1/auth/me");
