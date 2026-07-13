package org.hellorealworld.ping.product;

import java.util.ArrayList;
import java.util.List;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ProductQueryServiceTest {

	private final ProductQueryRepository repository = mock(ProductQueryRepository.class);
	private final ProductQueryService service = new ProductQueryService(repository);

	@Test
	void rejectsInvalidQueryValues() {
		assertThatThrownBy(() -> service.findProducts("invalid", 0, 1, 20, null, null))
				.isInstanceOf(IllegalArgumentException.class);
		assertThatThrownBy(() -> service.findProducts("books", 2, 1, 20, null, null))
				.isInstanceOf(IllegalArgumentException.class);
		assertThatThrownBy(() -> service.findProducts("books", 0, 1, 10, null, null))
				.isInstanceOf(IllegalArgumentException.class);
		assertThatThrownBy(() -> service.findProducts("books", 0, 1, 20, 1, null))
				.isInstanceOf(IllegalArgumentException.class);
		assertThatThrownBy(() -> service.findProducts("books", 0, 1, 20, 1, 0L))
				.isInstanceOf(IllegalArgumentException.class);
	}

	@Test
	void returnsLimitItemsAndBuildsCursorFromLastReturnedItemWhenMoreResultsExist() {
		List<ProductRecord> products = products(51);
		when(repository.findActiveProducts("books", 0, 100_000, 20, null, null))
				.thenReturn(products);

		ProductPageResponse response = service.findProducts("books", 0, 100_000, 20, null, null);

		assertThat(response.items()).hasSize(20);
		assertThat(response.nextCursor()).isEqualTo(new ProductPageResponse.Cursor(20, 20L));
		verify(repository).findActiveProducts("books", 0, 100_000, 20, null, null);
	}

	@Test
	void omitsCursorWhenFetchedResultsDoNotExceedLimit() {
		when(repository.findActiveProducts("books", 0, 100_000, 20, 500, 99L))
				.thenReturn(products(20));

		ProductPageResponse response = service.findProducts("books", 0, 100_000, 20, 500, 99L);

		assertThat(response.items()).hasSize(20);
		assertThat(response.nextCursor()).isNull();
	}

	@Test
	void mapsProductRecordToResponse() {
		ProductResponse response = ProductResponse.from(
				new ProductRecord(42L, "SKU-000042", "Product 000042", "books", 1299, 4567, true)
		);

		assertThat(response).isEqualTo(new ProductResponse(
				42L, "SKU-000042", "Product 000042", "books", 1299, 4567
		));
	}

	private List<ProductRecord> products(int count) {
		List<ProductRecord> products = new ArrayList<>(count);
		for (long id = 1; id <= count; id++) {
			products.add(new ProductRecord(id, "SKU-" + id, "Product " + id, "books", (int) id, 4000, true));
		}
		return products;
	}
}
