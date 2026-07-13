package org.hellorealworld.ping.product;

import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class ProductQueryControllerTest {

	@Test
	void rejectsInvalidQueryParameters() throws Exception {
		ProductQueryRepository repository = org.mockito.Mockito.mock(ProductQueryRepository.class);
		MockMvc mockMvc = MockMvcBuilders.standaloneSetup(
				new ProductQueryController(new ProductQueryService(repository))
		).build();

		mockMvc.perform(get("/products")
						.param("category", "invalid")
						.param("minPriceCents", "100")
						.param("maxPriceCents", "200")
						.param("limit", "20"))
				.andExpect(status().isBadRequest());

		mockMvc.perform(get("/products")
						.param("category", "books")
						.param("minPriceCents", "100")
						.param("maxPriceCents", "200")
						.param("limit", "20")
						.param("afterPriceCents", "150"))
				.andExpect(status().isBadRequest());
	}
}
