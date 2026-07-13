package org.hellorealworld.ping.product;

import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@Profile("read-heavy")
@RestController
class ProductQueryController {

	private final ProductQueryService service;

	ProductQueryController(ProductQueryService service) {
		this.service = service;
	}

	@GetMapping("/products")
	ProductPageResponse products(
			@RequestParam String category,
			@RequestParam int minPriceCents,
			@RequestParam int maxPriceCents,
			@RequestParam int limit,
			@RequestParam(required = false) Integer afterPriceCents,
			@RequestParam(required = false) Long afterId) {
		return service.findProducts(category, minPriceCents, maxPriceCents, limit,
				afterPriceCents, afterId);
	}

	@ResponseStatus(HttpStatus.BAD_REQUEST)
	@ExceptionHandler(IllegalArgumentException.class)
	void invalidQuery() {
	}
}
