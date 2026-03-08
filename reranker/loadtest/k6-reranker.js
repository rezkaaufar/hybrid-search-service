/**
 * k6 load test for the Reranker Service
 *
 * Usage (standalone):
 *   k6 run loadtest/k6-reranker.js
 *
 * Usage (via Docker Compose):
 *   docker compose --profile loadtest run --rm k6
 *
 * Tunables (env vars):
 *   BASE_URL  – reranker base URL  (default: http://localhost:8080)
 *   RPS       – target requests/s  (default: 20)
 *   DURATION  – test duration       (default: 2m)
 *   TOP_K     – top_k per request   (default: 5)
 *   VUS       – pre-allocated VUs   (default: 50)
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";
const RPS = parseInt(__ENV.RPS || "20", 10);
const DURATION = __ENV.DURATION || "2m";
const TOP_K = parseInt(__ENV.TOP_K || "5", 10);
const VUS = parseInt(__ENV.VUS || "50", 10);
const SLEEP = parseFloat(__ENV.SLEEP || "0");

// ---------------------------------------------------------------------------
// Custom metrics
// ---------------------------------------------------------------------------

const rerank_success = new Counter("rerank_success");
const rerank_failures = new Counter("rerank_failures");
const rerank_duration_ms = new Trend("rerank_duration_ms", true);

// ---------------------------------------------------------------------------
// k6 options
// ---------------------------------------------------------------------------

export const options = {
  scenarios: {
    constant_rps: {
      executor: "constant-arrival-rate",
      rate: RPS,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: VUS,
      maxVUs: VUS * 4,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],       // < 1 % errors
    http_req_duration: ["p(95)<2000", "p(99)<4000"],
    rerank_duration_ms: ["p(95)<2000"],
  },
};

// ---------------------------------------------------------------------------
// Sample queries and documents (simulating retrieval service output)
// ---------------------------------------------------------------------------

const QUERIES = [
  "battery life for wireless headphones",
  "best stroller for travel with newborn",
  "cat food for sensitive stomach",
  "gaming mouse with low latency",
  "waterproof hiking boots for wide feet",
  "organic baby formula ingredients",
  "noise cancelling earbuds under 50 dollars",
  "pet hair vacuum cleaner for hardwood floors",
  "lightweight laptop for college students",
  "bluetooth speaker waterproof outdoor",
];

const SAMPLE_DOCS = [
  {
    chunk_id: 1,
    document_id: 1,
    content:
      "These headphones have amazing battery life. I get over 30 hours on a single charge and the sound quality is excellent.",
    score: 0.92,
    source_title: "Review: Sony WH-1000XM5",
    source_url: "https://example.com/review/1",
  },
  {
    chunk_id: 2,
    document_id: 2,
    content:
      "Battery died after just 4 hours. Very disappointed with the charging time as well.",
    score: 0.85,
    source_title: "Review: Generic Headphones",
    source_url: "https://example.com/review/2",
  },
  {
    chunk_id: 3,
    document_id: 3,
    content:
      "Great stroller, folds easily and fits in the overhead bin on planes. Perfect for travel.",
    score: 0.80,
    source_title: "Review: Baby Jogger City Mini",
    source_url: "https://example.com/review/3",
  },
  {
    chunk_id: 4,
    document_id: 4,
    content:
      "My cat loves this food and her stomach issues cleared up within a week of switching.",
    score: 0.78,
    source_title: "Review: Hill's Science Diet",
    source_url: "https://example.com/review/4",
  },
  {
    chunk_id: 5,
    document_id: 5,
    content:
      "The mouse response time is incredibly fast. No lag at all even in competitive games.",
    score: 0.75,
    source_title: "Review: Logitech G Pro X",
    source_url: "https://example.com/review/5",
  },
  {
    chunk_id: 6,
    document_id: 6,
    content:
      "Comfortable fit, good arch support, and completely waterproof. My feet stayed dry after a 10-mile hike in the rain.",
    score: 0.72,
    source_title: "Review: Merrell Moab 3",
    source_url: "https://example.com/review/6",
  },
  {
    chunk_id: 7,
    document_id: 7,
    content:
      "All organic ingredients, no artificial preservatives. My baby has been happy and healthy on this formula.",
    score: 0.70,
    source_title: "Review: Happy Baby Organic Formula",
    source_url: "https://example.com/review/7",
  },
  {
    chunk_id: 8,
    document_id: 8,
    content:
      "The earbuds block out almost all noise. Perfect for open offices and flights.",
    score: 0.68,
    source_title: "Review: Anker Soundcore Q45",
    source_url: "https://example.com/review/8",
  },
  {
    chunk_id: 9,
    document_id: 9,
    content:
      "Picked up an entire sofa worth of pet hair in one pass. The suction is insane.",
    score: 0.65,
    source_title: "Review: Dyson V15 Detect",
    source_url: "https://example.com/review/9",
  },
  {
    chunk_id: 10,
    document_id: 10,
    content:
      "Thin, light, and the battery lasts all day. Great for taking notes in class.",
    score: 0.62,
    source_title: "Review: Dell XPS 13",
    source_url: "https://example.com/review/10",
  },
];

// ---------------------------------------------------------------------------
// Test logic
// ---------------------------------------------------------------------------

export default function () {
  const query = QUERIES[Math.floor(Math.random() * QUERIES.length)];

  // Shuffle docs and pick a random subset (3–10) to simulate real retrieval output
  const shuffled = SAMPLE_DOCS.slice().sort(() => Math.random() - 0.5);
  const numDocs = Math.floor(Math.random() * 8) + 3; // 3..10
  const documents = shuffled.slice(0, numDocs);

  const payload = JSON.stringify({
    query: query,
    documents: documents,
    top_k: TOP_K,
  });

  const params = {
    headers: { "Content-Type": "application/json" },
    timeout: "10s",
  };

  const t0 = Date.now();
  const res = http.post(`${BASE_URL}/rerank`, payload, params);
  const elapsed = Date.now() - t0;

  const ok = check(res, {
    "status 200": (r) => r.status === 200,
    "has results": (r) => {
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body.results) && body.results.length > 0;
      } catch {
        return false;
      }
    },
    "results have reranker_score": (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.results.every((d) => typeof d.reranker_score === "number");
      } catch {
        return false;
      }
    },
  });

  if (ok) {
    rerank_success.add(1);
  } else {
    rerank_failures.add(1);
  }

  rerank_duration_ms.add(elapsed);

  if (SLEEP > 0) {
    sleep(SLEEP);
  }
}
