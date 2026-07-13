import http from 'k6/http';
import { check } from 'k6';

const vus = Number(__ENV.VUS || '50');
const duration = __ENV.DURATION || '30s';
const baseUrl = __ENV.BASE_URL || 'http://localhost:8080';
const summaryTrendStats = ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'];

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
  const response = http.get(`${baseUrl}/ping`);

  check(response, {
    'status is 200': (r) => r.status === 200,
    'body contains pong': (r) => r.body && r.body.includes('pong'),
  });
}
