package org.hellorealworld.aggregate;

public record InventoryStatus(String sku, boolean available, int quantity) {
}
