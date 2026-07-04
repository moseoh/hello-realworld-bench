import http from 'k6/http';
import { check } from 'k6';

const vus = Number(__ENV.VUS || '25');
const duration = __ENV.DURATION || '45s';
const baseUrl = __ENV.BASE_URL || 'http://localhost:8080';

const customers = ['customer-001', 'customer-002', 'customer-003', 'customer-004', 'customer-005'];
const skus = ['SKU-001', 'SKU-002', 'SKU-003', 'SKU-004', 'SKU-005'];

export const options = {
  vus,
  duration,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const customerId = customers[Math.floor(Math.random() * customers.length)];
  const sku = skus[Math.floor(Math.random() * skus.length)];
  const quantity = 1 + Math.floor(Math.random() * 3);
  const unitPriceCents = 500 + Math.floor(Math.random() * 2500);

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
