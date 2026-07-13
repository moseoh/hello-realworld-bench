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
const priceInverse = 17679;

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
  const tupleIndex = Math.floor(iteration / 4);
  const category = categories[tupleIndex % categories.length];
  const priceWindow = priceWindows[
    Math.floor(tupleIndex / categories.length) % priceWindows.length
  ];
  const limit = pageSizes[
    Math.floor(tupleIndex / (categories.length * priceWindows.length)) % pageSizes.length
  ];
  const continuation = iteration % 4 === 3;
  const cursor = continuation ? firstPageCursor(category, priceWindow, limit) : null;
  const query = [
    `category=${category}`,
    `minPriceCents=${priceWindow.minPriceCents}`,
    `maxPriceCents=${priceWindow.maxPriceCents}`,
    `limit=${limit}`,
  ];

  if (cursor) {
    query.push(`afterPriceCents=${cursor.priceCents}`, `afterId=${cursor.id}`);
  }

  return {
    category,
    minPriceCents: priceWindow.minPriceCents,
    maxPriceCents: priceWindow.maxPriceCents,
    limit,
    cursor,
    query: query.join('&'),
  };
}

function firstPageCursor(category, priceWindow, limit) {
  let matched = 0;
  for (
    let priceCents = priceWindow.minPriceCents;
    priceCents <= priceWindow.maxPriceCents;
    priceCents += 1
  ) {
    const residue = priceCents - 500;
    const id = (residue * priceInverse) % 100000 || 100000;
    const categoryIndex = (
      ((id - 1) * 17 + Math.floor((id - 1) / 8)) % categories.length
    );
    if (categories[categoryIndex] !== category || id % 20 === 0) {
      continue;
    }
    matched += 1;
    if (matched === limit) {
      return { priceCents, id };
    }
  }
  throw new Error('Query contract does not contain a full first page.');
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
    'items match the requested category and price range': (r) => r.json('items').every((item) => (
      item.category === request.category
      && item.priceCents >= request.minPriceCents
      && item.priceCents <= request.maxPriceCents
    )),
    'items have product fields': (r) => r.json('items').every((item) => (
      Number.isInteger(item.id)
      && typeof item.sku === 'string'
      && typeof item.name === 'string'
      && typeof item.category === 'string'
      && Number.isInteger(item.priceCents)
      && Number.isInteger(item.ratingBasisPoints)
    )),
    'items are strictly ordered by price and id': (r) => r.json('items').every((item, index, items) => {
      if (index === 0) {
        return true;
      }
      const previous = items[index - 1];
      return item.priceCents > previous.priceCents || (
        item.priceCents === previous.priceCents && item.id > previous.id
      );
    }),
    'continuation starts after its cursor': (r) => {
      if (!request.cursor) {
        return true;
      }
      return r.json('items').every((item) => (
        item.priceCents > request.cursor.priceCents || (
          item.priceCents === request.cursor.priceCents
          && item.id > request.cursor.id
        )
      ));
    },
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
