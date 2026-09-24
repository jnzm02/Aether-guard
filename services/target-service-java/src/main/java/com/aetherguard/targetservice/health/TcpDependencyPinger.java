package com.aetherguard.targetservice.health;

import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;

/**
 * A {@link DependencyPinger} that verifies reachability with a short TCP connect
 * to the dependency's host:port. This keeps the JVM demo dependency-free (no
 * JDBC / Redis client) while still exercising the health-check contract.
 */
public class TcpDependencyPinger implements DependencyPinger {

    private final String name;
    private final String host;
    private final int port;

    public TcpDependencyPinger(String name, String url) {
        this.name = name;
        URI uri = URI.create(url.contains("://") ? url : "tcp://" + url);
        this.host = uri.getHost() != null ? uri.getHost() : "localhost";
        this.port = uri.getPort() > 0 ? uri.getPort() : defaultPort(name);
    }

    private static int defaultPort(String name) {
        return "redis".equals(name) ? 6379 : 5432;
    }

    @Override
    public String name() {
        return name;
    }

    @Override
    public void ping() throws Exception {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress(host, port), 2000);
        }
    }
}
