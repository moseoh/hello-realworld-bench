package org.hellorealworld.query;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "catalog_products")
class ProductEntity {

    @Id
    long id;

    @Column(nullable = false)
    String sku;

    @Column(nullable = false)
    String name;

    @Column(nullable = false)
    String category;

    @Column(name = "price_cents", nullable = false)
    int priceCents;

    @Column(name = "rating_basis_points", nullable = false)
    int ratingBasisPoints;

    @Column(nullable = false)
    boolean active;
}
