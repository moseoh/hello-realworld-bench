import http from 'k6/http';
import { check } from 'k6';

const vus = Number(__ENV.VUS || '25');
const duration = __ENV.DURATION || '45s';
const baseUrl = __ENV.BASE_URL || 'http://localhost:8080';
const summaryTrendStats = ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'];

const customers = ['customer-001', 'customer-002', 'customer-003', 'customer-004'];
const skus = ['SKU-001', 'SKU-002', 'SKU-003', 'SKU-004'];

function loadOptions() {
  const executor = __ENV.HRW_LOAD_EXECUTOR;
  if (!executor) {
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
  const iteration = (__VU - 1) * 1000003 + __ITER;
  const customerId = customers[iteration % customers.length];
  const sku = skus[Math.floor(iteration / customers.length) % skus.length];
  const response = http.get(`${baseUrl}/aggregate?customerId=${customerId}&sku=${sku}`);

  check(response, {
    'status is 200': (r) => r.status === 200,
    'customer exists': (r) => Boolean(r.json('customer.id')),
    'recommendations exist': (r) => Array.isArray(r.json('recommendations')),
    'inventory exists': (r) => typeof r.json('inventory.available') === 'boolean',
  });
}
