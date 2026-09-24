package com.aetherguard.targetservice.api;

import com.aetherguard.targetservice.api.model.Order;
import com.aetherguard.targetservice.api.model.User;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Plain in-memory data store — the JVM demo keeps no real database (no JDBC /
 * H2) to minimise moving parts. The {@link ApiController} still times reads into
 * the {@code aether_guard_db_query_duration_seconds} timer for metric parity.
 */
@Component
public class InMemoryStore {

    private final List<User> users = List.of(
            new User(1, "Alice Johnson", "alice@example.com"),
            new User(2, "Bob Smith", "bob@example.com"),
            new User(3, "Carol Williams", "carol@example.com"),
            new User(4, "Dave Brown", "dave@example.com"),
            new User(5, "Eve Davis", "eve@example.com"));

    private final List<Order> orders = List.of(
            new Order(1, 1, "Alice Johnson", "Widget", 19.99, "delivered"),
            new Order(2, 1, "Alice Johnson", "Gadget", 49.50, "processing"),
            new Order(3, 2, "Bob Smith", "Gizmo", 12.00, "pending"),
            new Order(4, 3, "Carol Williams", "Doohickey", 99.99, "delivered"),
            new Order(5, 4, "Dave Brown", "Sprocket", 5.75, "processing"),
            new Order(6, 5, "Eve Davis", "Cog", 250.00, "shipped"),
            new Order(7, 2, "Bob Smith", "Widget", 19.99, "pending"));

    public List<User> users() {
        return users;
    }

    public List<Order> orders() {
        return orders;
    }
}
