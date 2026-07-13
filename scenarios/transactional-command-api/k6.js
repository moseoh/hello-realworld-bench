import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Counter, Gauge, Trend } from 'k6/metrics';

const vus = Number(__ENV.VUS || '25');
const duration = __ENV.DURATION || '45s';
const baseUrl = __ENV.BASE_URL || 'http://localhost:8080';
const summaryTrendStats = ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'];
const timelineBucketMs = 10000;
const timelineBucketCount = Math.ceil(durationMilliseconds(duration) / timelineBucketMs);
const timelineRequests = new Counter('hrw_timeline_requests');
const timelineFailures = new Counter('hrw_timeline_failures');
const timelineDuration = new Trend('hrw_timeline_duration', true);
const timelineOrigin = new Gauge('hrw_timeline_origin_ms');

const customers = ['customer-001', 'customer-002', 'customer-003', 'customer-004', 'customer-005'];
const skus = ['SKU-001', 'SKU-002', 'SKU-003', 'SKU-004', 'SKU-005'];

function loadOptions() {
  const timeline = { thresholds: timelineThresholds() };
  const executor = __ENV.HRW_LOAD_EXECUTOR;
  if (!executor || executor === 'constant-vus') {
    return { vus, duration, summaryTrendStats, ...timeline };
  }

  const rate = Number(__ENV.HRW_LOAD_RATE || vus);
  const preAllocatedVUs = Number(__ENV.HRW_LOAD_PRE_ALLOCATED_VUS || vus);
  const maxVUs = Number(__ENV.HRW_LOAD_MAX_VUS || preAllocatedVUs);
  const scenario = {
    executor,
    timeUnit: '1s',
    preAllocatedVUs,
    maxVUs,
  };

  if (executor === 'constant-arrival-rate') {
    Object.assign(scenario, { rate, duration });
  } else if (executor === 'ramping-arrival-rate') {
    Object.assign(scenario, {
      startRate: rate,
      stages: JSON.parse(__ENV.HRW_LOAD_STAGES || JSON.stringify([{ duration, target: rate }])),
    });
  } else {
    throw new Error(`Unsupported HRW_LOAD_EXECUTOR: ${executor}`);
  }

  return { scenarios: { default: scenario }, summaryTrendStats, ...timeline };
}

function timelineThresholds() {
  const thresholds = {};
  for (let bucket = 0; bucket < timelineBucketCount; bucket += 1) {
    thresholds[`hrw_timeline_requests{bucket:${bucket}}`] = ['count>=0'];
    thresholds[`hrw_timeline_failures{bucket:${bucket}}`] = ['count>=0'];
    thresholds[`hrw_timeline_duration{bucket:${bucket}}`] = ['med>=0'];
  }
  return thresholds;
}

function durationMilliseconds(value) {
  const match = /^(\d+)(s|m|h)$/.exec(value);
  if (!match) {
    throw new Error(`Unsupported duration: ${value}`);
  }
  return Number(match[1]) * { s: 1000, m: 60000, h: 3600000 }[match[2]];
}

function timelineBucket() {
  return Math.min(
    timelineBucketCount - 1,
    Math.max(0, Math.floor((Date.now() - exec.scenario.startTime) / timelineBucketMs)),
  );
}

function recordTimeline(bucket, response, valid) {
  const tags = { bucket: String(bucket) };
  timelineOrigin.add(exec.scenario.startTime);
  timelineRequests.add(1, tags);
  timelineDuration.add(response.timings.duration, tags);
  if (!valid) {
    timelineFailures.add(1, tags);
  }
}

export const options = loadOptions();

export default function () {
  const bucket = timelineBucket();
  const iteration = exec.scenario.iterationInTest;
  const customerId = customers[iteration % customers.length];
  const sku = skus[Math.floor(iteration / customers.length) % skus.length];
  const quantity = 1 + (iteration % 3);
  const unitPriceCents = 500 + ((iteration * 997) % 2500);

  const response = http.post(
    `${baseUrl}/orders`,
    JSON.stringify({
      customerId,
      items: [{ sku, quantity, unitPriceCents }],
    }),
    {
      headers: { 'Content-Type': 'application/json' },
    },
  );

  const valid = check(response, {
    'status is 201': (r) => r.status === 201,
    'order accepted': (r) => r.json('status') === 'accepted',
    'order id exists': (r) => Boolean(r.json('orderId')),
  });
  recordTimeline(bucket, response, valid);
}
