import http from 'k6/http';
import { check } from 'k6';

const vus = Number(__ENV.VUS || '5');
const duration = __ENV.DURATION || '45s';
const baseUrl = __ENV.BASE_URL || 'http://localhost:8080';

const customers = ['customer-001', 'customer-002', 'customer-003', 'customer-004'];
const skus = ['SKU-001', 'SKU-002', 'SKU-003', 'SKU-004'];

export const options = {
  vus,
  duration,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const customerId = customers[Math.floor(Math.random() * customers.length)];
  const sku = skus[Math.floor(Math.random() * skus.length)];
  const response = http.get(`${baseUrl}/aggregate?customerId=${customerId}&sku=${sku}`);

  check(response, {
    'status is 200': (r) => r.status === 200,
    'customer exists': (r) => Boolean(r.json('customer.id')),
    'recommendations exist': (r) => Array.isArray(r.json('recommendations')),
    'inventory fallback used': (r) => r.json('inventory.available') === false,
  });
}
