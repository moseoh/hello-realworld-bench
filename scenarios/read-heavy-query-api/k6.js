import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';

const vus = Number(__ENV.VUS || '25');
const duration = __ENV.DURATION || '45s';
const baseUrl = __ENV.BASE_URL || 'http://localhost:8080';
const summaryTrendStats = ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'];
const categories = [
  'electronics',
  'home',
  'books',
  'sports',
  'beauty',
  'toys',
  'automotive',
  'garden',
];
const priceWindows = [
  { minPriceCents: 500, maxPriceCents: 25499 },
  { minPriceCents: 25500, maxPriceCents: 50499 },
  { minPriceCents: 50500, maxPriceCents: 75499 },
  { minPriceCents: 75500, maxPriceCents: 100499 },
];
const pageSizes = [20, 50];

function loadOptions() {
  const executor = __ENV.HRW_LOAD_EXECUTOR;
  if (!executor || executor === 'constant-vus') {
    return { vus, duration, summaryTrendStats };
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

  return { scenarios: { default: scenario }, summaryTrendStats };
}

export const options = loadOptions();

function buildRequest(iteration) {
  const category = categories[iteration % categories.length];
  const priceWindow = priceWindows[Math.floor(iteration / categories.length) % priceWindows.length];
  const limit = pageSizes[Math.floor(iteration / (categories.length * priceWindows.length)) % pageSizes.length];
  const query = [
    `category=${category}`,
    `minPriceCents=${priceWindow.minPriceCents}`,
    `maxPriceCents=${priceWindow.maxPriceCents}`,
    `limit=${limit}`,
  ];

  if (iteration % 4 === 3) {
    query.push(`afterPriceCents=${priceWindow.minPriceCents}`, 'afterId=1');
  }

  return { limit, query: query.join('&') };
}

export default function () {
  const iteration = exec.scenario.iterationInTest;
  const request = buildRequest(iteration);
  const query = request.query;
  const response = http.get(`${baseUrl}/products?${query}`);

  check(response, {
    'status is 200': (r) => r.status === 200,
    'response is bounded': (r) => r.body.length <= 16384,
    'item count is bounded': (r) => {
      const body = r.json();
      return Array.isArray(body.items) && body.items.length <= request.limit;
    },
    'items have product fields': (r) => r.json('items').every((item) => (
      Number.isInteger(item.id)
      && typeof item.sku === 'string'
      && typeof item.name === 'string'
      && typeof item.category === 'string'
      && Number.isInteger(item.priceCents)
      && Number.isInteger(item.ratingBasisPoints)
    )),
    'cursor is null or matches final item': (r) => {
      const body = r.json();
      const lastItem = body.items[body.items.length - 1];
      return body.nextCursor === null || (
        body.items.length > 0
        && body.nextCursor.priceCents === lastItem.priceCents
        && body.nextCursor.id === lastItem.id
      );
    },
  });
}
