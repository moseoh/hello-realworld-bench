package org.hellorealworld.ping.aggregate;

record InventoryStatus(String sku, boolean available, int quantity) {
}
