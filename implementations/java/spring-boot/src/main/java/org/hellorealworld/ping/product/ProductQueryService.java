package org.hellorealworld.ping.product;

import java.util.List;
import java.util.Set;

import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Profile("read-heavy")
@Service
class ProductQueryService {

	private static final Set<String> CATEGORIES = Set.of(
			"electronics", "home", "books", "sports", "beauty", "toys", "automotive", "garden"
	);

	private final ProductQueryRepository repository;

	ProductQueryService(ProductQueryRepository repository) {
		this.repository = repository;
	}

	@Transactional(readOnly = true)
	ProductPageResponse findProducts(String category, int minPriceCents, int maxPriceCents,
			int limit, Integer afterPriceCents, Long afterId) {
		validate(category, minPriceCents, maxPriceCents, limit, afterPriceCents, afterId);
		List<ProductRecord> products = repository.findActiveProducts(
				category, minPriceCents, maxPriceCents, limit, afterPriceCents, afterId);
		boolean hasNext = products.size() > limit;
		List<ProductRecord> page = hasNext ? products.subList(0, limit) : products;
		List<ProductResponse> items = page.stream().map(ProductResponse::from).toList();
		ProductPageResponse.Cursor cursor = null;
		if (hasNext) {
			ProductRecord last = page.getLast();
			cursor = new ProductPageResponse.Cursor(last.priceCents(), last.id());
		}
		return new ProductPageResponse(items, cursor);
	}

	private static void validate(String category, int minPriceCents, int maxPriceCents, int limit,
			Integer afterPriceCents, Long afterId) {
		boolean cursorPaired = (afterPriceCents == null) == (afterId == null);
		if (!CATEGORIES.contains(category) || minPriceCents < 0 || maxPriceCents < minPriceCents
				|| (limit != 20 && limit != 50) || !cursorPaired
				|| (afterPriceCents != null && (afterPriceCents < 0 || afterId <= 0))) {
			throw new IllegalArgumentException("invalid product query");
		}
	}
}
