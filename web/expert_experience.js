"use strict";

(function (root) {
  const ACTIVE = new Set(["PENDING", "RUNNING"]);

  function createExpertExperienceController(options = {}) {
    const fetchImpl = options.fetch || root.fetch.bind(root);
    const schedule = options.setTimeout || root.setTimeout.bind(root);
    const cancel = options.clearTimeout || root.clearTimeout.bind(root);
    const jobs = new Map();
    let timer = null;
    let destroyed = false;

    function schedulePoll(delay = 900) {
      if (destroyed || timer !== null || ![...jobs.values()].some((job) => ACTIVE.has(job.status))) return;
      timer = schedule(poll, delay);
    }

    async function poll() {
      timer = null;
      const activeIds = [...jobs.values()].filter((job) => ACTIVE.has(job.status)).map((job) => job.job_id);
      await Promise.all(activeIds.map(async (jobId) => {
        try {
          const response = await fetchImpl(`/api/expert-experience-summary-jobs/${encodeURIComponent(jobId)}`);
          if (!response.ok) return;
          const job = await response.json();
          jobs.set(job.job_id, job);
          options.onJobChange?.(job);
          options.onCountChange?.(job.expert_experience_total_count);
        } catch (_) { /* retry on the shared next tick */ }
      }));
      schedulePoll();
    }

    function track(job) {
      if (!job?.job_id) return;
      jobs.set(job.job_id, { ...(jobs.get(job.job_id) || {}), ...job });
      options.onJobChange?.(jobs.get(job.job_id));
      schedulePoll(0);
    }

    function destroy() {
      destroyed = true;
      if (timer !== null) cancel(timer);
      timer = null;
      jobs.clear();
    }

    return { track, poll, destroy, getJobs: () => new Map(jobs), getTimerActive: () => timer !== null };
  }

  const api = { createExpertExperienceController };
  Object.assign(root, api);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);

