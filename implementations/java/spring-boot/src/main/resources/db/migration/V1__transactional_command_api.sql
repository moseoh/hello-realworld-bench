create table orders (
    id text primary key,
    customer_id text not null,
    status text not null,
    total_cents integer not null,
    created_at timestamptz not null
);

create table order_items (
    id text primary key,
    order_id text not null references orders(id),
    sku text not null,
    quantity integer not null,
    unit_price_cents integer not null
);

create table outbox_events (
    id text primary key,
    aggregate_type text not null,
    aggregate_id text not null,
    event_type text not null,
    payload_json jsonb not null,
    created_at timestamptz not null,
    published_at timestamptz null
);
