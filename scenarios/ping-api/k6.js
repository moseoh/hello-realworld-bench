import http from 'k6/http';
import { check } from 'k6';

const vus = Number(__ENV.VUS || '50');
const duration = __ENV.DURATION || '30s';
const baseUrl = __ENV.BASE_URL || 'http://localhost:8080';

export const options = {
  vus,
  duration,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const response = http.get(`${baseUrl}/ping`);

  check(response, {
    'status is 200': (r) => r.status === 200,
    'body contains pong': (r) => r.body && r.body.includes('pong'),
  });
}
