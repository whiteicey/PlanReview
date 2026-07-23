const test = require("node:test");
const assert = require("node:assert/strict");
const { createExpertExperienceController } = require("./expert_experience.js");

test("all active jobs share one polling timer", async () => {
  const scheduled = [];
  const controller = createExpertExperienceController({
    fetch: async (url) => ({ ok: true, json: async () => ({
      job_id: url.split("/").at(-1), status: "COMPLETED", expert_experience_total_count: 2,
    }) }),
    setTimeout: (callback) => { scheduled.push(callback); return scheduled.length; },
    clearTimeout: () => {},
  });
  controller.track({ job_id: "a", status: "PENDING" });
  controller.track({ job_id: "b", status: "RUNNING" });
  assert.equal(scheduled.length, 1);
  await scheduled[0]();
  assert.equal(controller.getJobs().get("a").status, "COMPLETED");
  assert.equal(controller.getJobs().get("b").status, "COMPLETED");
  controller.destroy();
});
