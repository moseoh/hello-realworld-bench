CREATE TABLE catalog_products (
  id BIGINT PRIMARY KEY,
  sku TEXT NOT NULL,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  price_cents INTEGER NOT NULL,
  rating_basis_points INTEGER NOT NULL,
  active BOOLEAN NOT NULL
);

INSERT INTO catalog_products (
  id,
  sku,
  name,
  category,
  price_cents,
  rating_basis_points,
  active
)
SELECT
  id,
  'SKU-' || lpad(id::text, 6, '0'),
  'Product ' || lpad(id::text, 6, '0'),
  (
    ARRAY[
      'electronics',
      'home',
      'books',
      'sports',
      'beauty',
      'toys',
      'automotive',
      'garden'
    ]
  )[((id - 1) * 17 + ((id - 1) / 8)) % 8 + 1],
  500 + ((id * 7919) % 100000),
  3000 + ((id * 37) % 2001),
  (id % 20) != 0
FROM generate_series(1, 100000) AS id;

CREATE INDEX idx_catalog_products_filter
  ON catalog_products (category, active, price_cents, id);

ANALYZE catalog_products;
