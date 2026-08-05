import { onScopeDispose, toValue, watch, type MaybeRefOrGetter } from "vue";

export function useReviewPolling(
  shouldPoll: MaybeRefOrGetter<boolean>,
  refresh: () => Promise<boolean | void>,
  intervalMs = 1_500,
) {
  let timer: number | undefined;

  function stop(): void {
    window.clearInterval(timer);
    timer = undefined;
  }

  function restart(): void {
    stop();
    if (!toValue(shouldPoll)) return;
    timer = window.setInterval(async () => {
      try {
        if ((await refresh()) === false) stop();
      } catch {
        stop();
      }
    }, intervalMs);
  }

  watch(() => toValue(shouldPoll), restart, { immediate: true });
  onScopeDispose(stop);

  return { restart, stop };
}
