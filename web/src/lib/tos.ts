export const TOS_STORAGE_KEY = "visitor_atlas_tos_v1";

export function readTosAccepted(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(TOS_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeTosAccepted(accepted: boolean): void {
  if (typeof window === "undefined") return;
  try {
    if (accepted) {
      window.localStorage.setItem(TOS_STORAGE_KEY, "1");
    } else {
      window.localStorage.removeItem(TOS_STORAGE_KEY);
    }
  } catch {
    // ignore quota / private mode
  }
}
