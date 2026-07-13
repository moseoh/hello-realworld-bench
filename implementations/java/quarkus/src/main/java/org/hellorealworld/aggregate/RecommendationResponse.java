package org.hellorealworld.aggregate;

import java.util.List;

public record RecommendationResponse(List<RecommendationItem> items) {
}
