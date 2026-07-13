package org.hellorealworld.query;

import java.util.List;
import java.util.Set;

import org.hibernate.Session;
import org.hibernate.query.SelectionQuery;

import io.quarkus.arc.InjectableInstance;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.inject.Any;
import jakarta.inject.Inject;
import jakarta.transaction.Transactional;
import jakarta.ws.rs.BadRequestException;

@ApplicationScoped
class ProductQueryService {

    private static final Set<String> CATEGORIES = Set.of(
            "electronics", "home", "books", "sports", "beauty", "toys", "automotive", "garden"
    );

    @Inject
    @Any
    InjectableInstance<Session> sessions;

    @Transactional
    ProductPageResponse findProducts(String category, int minPriceCents, int maxPriceCents,
            int limit, Integer afterPriceCents, Long afterId) {
        validate(category, minPriceCents, maxPriceCents, limit, afterPriceCents, afterId);
        String cursorPredicate = afterPriceCents == null ? "" : """
                and (product.priceCents > :afterPriceCents
                    or (product.priceCents = :afterPriceCents and product.id > :afterId))
                """;
        SelectionQuery<ProductEntity> query = sessions.getActive().createSelectionQuery("""
                select product
                from ProductEntity product
                where product.active = true
                    and product.category = :category
                    and product.priceCents between :minPriceCents and :maxPriceCents
                """ + cursorPredicate + """
                order by product.priceCents asc, product.id asc
                """, ProductEntity.class);
        query.setParameter("category", category);
        query.setParameter("minPriceCents", minPriceCents);
        query.setParameter("maxPriceCents", maxPriceCents);
        if (afterPriceCents != null) {
            query.setParameter("afterPriceCents", afterPriceCents);
            query.setParameter("afterId", afterId);
        }
        query.setReadOnly(true);
        query.setMaxResults(limit + 1);

        List<ProductEntity> products = query.getResultList();
        boolean hasNext = products.size() > limit;
        List<ProductEntity> page = hasNext ? products.subList(0, limit) : products;
        List<ProductResponse> items = page.stream().map(ProductResponse::from).toList();
        ProductPageResponse.Cursor cursor = null;
        if (hasNext) {
            ProductEntity last = page.getLast();
            cursor = new ProductPageResponse.Cursor(last.priceCents, last.id);
        }
        return new ProductPageResponse(items, cursor);
    }

    private static void validate(String category, int minPriceCents, int maxPriceCents, int limit,
            Integer afterPriceCents, Long afterId) {
        boolean cursorPaired = (afterPriceCents == null) == (afterId == null);
        if (!CATEGORIES.contains(category) || minPriceCents < 0 || maxPriceCents < minPriceCents
                || (limit != 20 && limit != 50) || !cursorPaired
                || (afterPriceCents != null && (afterPriceCents < 0 || afterId <= 0))) {
            throw new BadRequestException("invalid product query");
        }
    }
}
