package org.hellorealworld.query;

import java.util.List;

record ProductPageResponse(List<ProductResponse> items, Cursor nextCursor) {

    record Cursor(int priceCents, long id) {
    }
}
