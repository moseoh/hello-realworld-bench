package org.hellorealworld.ping.order;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;

@Entity
@Table(name = "order_items")
class OrderItemRecord {

	@Id
	private String id;

	@ManyToOne(fetch = FetchType.LAZY, optional = false)
	@JoinColumn(name = "order_id", nullable = false)
	private OrderRecord order;

	@Column(nullable = false)
	private String sku;

	@Column(nullable = false)
	private int quantity;

	@Column(name = "unit_price_cents", nullable = false)
	private int unitPriceCents;

	protected OrderItemRecord() {
	}

	OrderItemRecord(String id, OrderRecord order, String sku, int quantity, int unitPriceCents) {
		this.id = id;
		this.order = order;
		this.sku = sku;
		this.quantity = quantity;
		this.unitPriceCents = unitPriceCents;
	}
}
