import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';

const vus = Number(__ENV.VUS || '25');
const duration = __ENV.DURATION || '45s';
const baseUrl = __ENV.BASE_URL || 'http://localhost:8080';
const summaryTrendStats = ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'];

const customers = ['customer-001', 'customer-002', 'customer-003', 'customer-004', 'customer-005'];
const skus = ['SKU-001', 'SKU-002', 'SKU-003', 'SKU-004', 'SKU-005'];

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

export default function () {
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

  check(response, {
    'status is 201': (r) => r.status === 201,
    'order accepted': (r) => r.json('status') === 'accepted',
    'order id exists': (r) => Boolean(r.json('orderId')),
  });
}
