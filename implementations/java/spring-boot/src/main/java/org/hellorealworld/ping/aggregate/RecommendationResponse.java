package org.hellorealworld.ping.aggregate;

import java.util.List;

record RecommendationResponse(List<RecommendationItem> items) {
}
