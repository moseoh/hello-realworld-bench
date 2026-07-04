package org.hellorealworld.ping.order;

import java.time.Instant;

import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "outbox_events")
class OutboxEventRecord {

	@Id
	private String id;

	@Column(name = "aggregate_type", nullable = false)
	private String aggregateType;

	@Column(name = "aggregate_id", nullable = false)
	private String aggregateId;

	@Column(name = "event_type", nullable = false)
	private String eventType;

	@JdbcTypeCode(SqlTypes.JSON)
	@Column(name = "payload_json", nullable = false, columnDefinition = "jsonb")
	private String payloadJson;

	@Column(name = "created_at", nullable = false)
	private Instant createdAt;

	@Column(name = "published_at")
	private Instant publishedAt;

	protected OutboxEventRecord() {
	}

	OutboxEventRecord(
			String id,
			String aggregateType,
			String aggregateId,
			String eventType,
			String payloadJson,
			Instant createdAt
	) {
		this.id = id;
		this.aggregateType = aggregateType;
		this.aggregateId = aggregateId;
		this.eventType = eventType;
		this.payloadJson = payloadJson;
		this.createdAt = createdAt;
	}
}
