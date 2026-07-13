package org.hellorealworld.ping.product;

import java.util.List;

import org.hibernate.Session;
import org.hibernate.query.SelectionQuery;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Repository;

import jakarta.persistence.EntityManager;

@Profile("read-heavy")
@Repository
class ProductQueryRepository {

	private final EntityManager entityManager;

	ProductQueryRepository(EntityManager entityManager) {
		this.entityManager = entityManager;
	}

	List<ProductRecord> findActiveProducts(String category, int minPriceCents, int maxPriceCents,
			int limit, Integer afterPriceCents, Long afterId) {
		String cursorPredicate = afterPriceCents == null ? "" : """
				and (product.priceCents > :afterPriceCents
					or (product.priceCents = :afterPriceCents and product.id > :afterId))
				""";
		SelectionQuery<ProductRecord> query = entityManager.unwrap(Session.class).createSelectionQuery("""
				select product
				from ProductRecord product
				where product.active = true
					and product.category = :category
					and product.priceCents between :minPriceCents and :maxPriceCents
				""" + cursorPredicate + """
				order by product.priceCents asc, product.id asc
				""", ProductRecord.class);
		query.setParameter("category", category);
		query.setParameter("minPriceCents", minPriceCents);
		query.setParameter("maxPriceCents", maxPriceCents);
		if (afterPriceCents != null) {
			query.setParameter("afterPriceCents", afterPriceCents);
			query.setParameter("afterId", afterId);
		}
		query.setReadOnly(true);
		query.setMaxResults(limit + 1);
		return query.getResultList();
	}
}
