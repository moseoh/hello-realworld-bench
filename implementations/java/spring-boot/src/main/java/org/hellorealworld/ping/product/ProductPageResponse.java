package org.hellorealworld.ping.product;

import java.util.List;

record ProductPageResponse(List<ProductResponse> items, Cursor nextCursor) {

	record Cursor(int priceCents, long id) {
	}
}
