package org.hellorealworld.ping.order;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

import jakarta.persistence.CascadeType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.OneToMany;
import jakarta.persistence.Table;

@Entity
@Table(name = "orders")
class OrderRecord {

	@Id
	private String id;

	@Column(name = "customer_id", nullable = false)
	private String customerId;

	@Column(nullable = false)
	private String status;

	@Column(name = "total_cents", nullable = false)
	private int totalCents;

	@Column(name = "created_at", nullable = false)
	private Instant createdAt;

	@OneToMany(mappedBy = "order", cascade = CascadeType.ALL, orphanRemoval = true)
	private List<OrderItemRecord> items = new ArrayList<>();

	protected OrderRecord() {
	}

	OrderRecord(String id, String customerId, String status, int totalCents, Instant createdAt) {
		this.id = id;
		this.customerId = customerId;
		this.status = status;
		this.totalCents = totalCents;
		this.createdAt = createdAt;
	}

	void addItem(OrderItemRecord item) {
		this.items.add(item);
	}
}
