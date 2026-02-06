import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const MODE = __ENV.MODE || "hybrid"; // lexical | semantic | hybrid
const K = Number(__ENV.K || 5);
const SLEEP = Number(__ENV.SLEEP || 0);

// Provide a comma-separated list of query strings via QUERIES env if you like.
const defaultQueries = [
  "battery life for this camera",
  "best stroller for travel",
  "wireless headset mic quality",
  "gaming mouse latency",
  "cat food for sensitive stomach",
];
const queries = (__ENV.QUERIES && __ENV.QUERIES.split(",")) || defaultQueries;

// Metrics for richer dashboard/CLI output
const success = new Counter("query_success");
const failures = new Counter("query_failures");
const fusionLatency = new Trend("query_duration_ms");

export const options = {
  // Default: constant arrival rate; override with env vars.
  scenarios: {
    constant_load: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.RPS || 20), // requests per second
      timeUnit: "1s",
      duration: __ENV.DURATION || "2m",
      preAllocatedVUs: Number(__ENV.VUS || 50),
      maxVUs: Number(__ENV.MAX_VUS || 200),
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<1000", "p(99)<2000"],
    query_success: ["count>0"],
  },
};

export default function () {
  const query = queries[Math.floor(Math.random() * queries.length)].trim();
  const payload = JSON.stringify({ query, k: K, mode: MODE });
  const res = http.post(`${BASE_URL}/query`, payload, {
    headers: { "Content-Type": "application/json" },
  });

  const ok =
    res.status === 200 &&
    res.json("results") !== undefined &&
    Array.isArray(res.json("results"));

  check(res, {
    "status is 200": () => res.status === 200,
    "has results array": () => Array.isArray(res.json("results")),
  });

  if (ok) {
    success.add(1);
    fusionLatency.add(res.timings.duration);
  } else {
    failures.add(1);
  }

  if (SLEEP > 0) {
    sleep(SLEEP);
  }
}
