package org.hellorealworld.ping.aggregate;

import java.util.concurrent.CompletableFuture;
import java.util.function.Supplier;

interface AggregationOperationExecutor {

	<T> CompletableFuture<T> submit(Supplier<T> operation);
}
