export function useSessionId() {
  return new URLSearchParams(window.location.search).get("session_id");
}
