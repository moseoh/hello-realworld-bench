package org.hellorealworld.ping.product;

record ProductResponse(long id, String sku, String name, String category, int priceCents,
		int ratingBasisPoints) {

	static ProductResponse from(ProductRecord product) {
		return new ProductResponse(product.id(), product.sku(), product.name(), product.category(),
				product.priceCents(), product.ratingBasisPoints());
	}
}
