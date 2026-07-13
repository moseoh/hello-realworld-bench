package org.hellorealworld.ping.product;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "catalog_products")
class ProductRecord {

	@Id
	private long id;

	@Column(nullable = false)
	private String sku;

	@Column(nullable = false)
	private String name;

	@Column(nullable = false)
	private String category;

	@Column(name = "price_cents", nullable = false)
	private int priceCents;

	@Column(name = "rating_basis_points", nullable = false)
	private int ratingBasisPoints;

	@Column(nullable = false)
	private boolean active;

	protected ProductRecord() {
	}

	ProductRecord(long id, String sku, String name, String category, int priceCents,
			int ratingBasisPoints, boolean active) {
		this.id = id;
		this.sku = sku;
		this.name = name;
		this.category = category;
		this.priceCents = priceCents;
		this.ratingBasisPoints = ratingBasisPoints;
		this.active = active;
	}

	long id() {
		return id;
	}

	String sku() {
		return sku;
	}

	String name() {
		return name;
	}

	String category() {
		return category;
	}

	int priceCents() {
		return priceCents;
	}

	int ratingBasisPoints() {
		return ratingBasisPoints;
	}
}
