package com.aishop.middleware;

import com.rabbitmq.client.ConnectionFactory;
import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.MigrationVersion;
import org.junit.jupiter.api.Test;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.containers.RabbitMQContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@Testcontainers
class MiddlewareIT {

    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.0.36")
            .withDatabaseName("aishop")
            .withUsername("aishop")
            .withPassword("aishop");

    @Container
    static final GenericContainer<?> REDIS = new GenericContainer<>(
            DockerImageName.parse("redis:7.2-alpine"))
            .withExposedPorts(6379);

    @Container
    static final RabbitMQContainer RABBITMQ = new RabbitMQContainer(
            DockerImageName.parse("rabbitmq:3.13-management-alpine"))
            .withAdminUser("aishop")
            .withAdminPassword("aishop");

    @Container
    static final GenericContainer<?> ELASTICSEARCH = new GenericContainer<>(
            DockerImageName.parse("docker.elastic.co/elasticsearch/elasticsearch:8.15.3"))
            .withEnv("discovery.type", "single-node")
            .withEnv("xpack.security.enabled", "false")
            .withEnv("xpack.license.self_generated.type", "basic")
            .withEnv("ES_JAVA_OPTS", "-Xms512m -Xmx512m")
            .withExposedPorts(9200);

    @Test
    void mysqlEnforcesIdempotencyAndAtomicStockUnderConcurrency() throws Exception {
        try (Connection connection = mysqlConnection();
             Statement statement = connection.createStatement()) {
            statement.executeUpdate("""
                    CREATE TABLE IF NOT EXISTS middleware_request (
                      id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                      user_id BIGINT NOT NULL,
                      command_type VARCHAR(32) NOT NULL,
                      idempotency_key VARCHAR(64) NOT NULL,
                      payload_hash CHAR(64) NOT NULL,
                      UNIQUE KEY uq_request (user_id, command_type, idempotency_key)
                    ) ENGINE=InnoDB
                    """);
            statement.executeUpdate("""
                    CREATE TABLE IF NOT EXISTS middleware_stock (
                      id BIGINT NOT NULL PRIMARY KEY,
                      stock INT NOT NULL
                    ) ENGINE=InnoDB
                    """);
            statement.executeUpdate("DELETE FROM middleware_request");
            statement.executeUpdate("DELETE FROM middleware_stock");
            statement.executeUpdate("INSERT INTO middleware_stock(id, stock) VALUES (1, 1)");
        }

        int workers = 12;
        ExecutorService pool = Executors.newFixedThreadPool(workers);
        CountDownLatch ready = new CountDownLatch(workers);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Integer>> idempotencyResults = new ArrayList<>();
        List<Future<Integer>> stockResults = new ArrayList<>();
        for (int i = 0; i < workers; i++) {
            idempotencyResults.add(pool.submit(() -> {
                ready.countDown();
                start.await();
                try (Connection connection = mysqlConnection();
                     PreparedStatement insert = connection.prepareStatement("""
                             INSERT INTO middleware_request
                               (user_id, command_type, idempotency_key, payload_hash)
                             VALUES (?, ?, ?, ?)
                             ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
                             """);
                     PreparedStatement select = connection.prepareStatement(
                             "SELECT id FROM middleware_request WHERE user_id=? AND command_type=? AND idempotency_key=?")) {
                    insert.setLong(1, 7L);
                    insert.setString(2, "ORDER");
                    insert.setString(3, "same-request-key");
                    insert.setString(4, "hash-a");
                    insert.executeUpdate();
                    select.setLong(1, 7L);
                    select.setString(2, "ORDER");
                    select.setString(3, "same-request-key");
                    try (ResultSet result = select.executeQuery()) {
                        assertTrue(result.next());
                        return result.getInt(1);
                    }
                }
            }));
            stockResults.add(pool.submit(() -> {
                ready.countDown();
                start.await();
                try (Connection connection = mysqlConnection();
                     PreparedStatement update = connection.prepareStatement(
                             "UPDATE middleware_stock SET stock=stock-1 WHERE id=1 AND stock >= 1")) {
                    return update.executeUpdate();
                }
            }));
        }
        assertTrue(ready.await(10, TimeUnit.SECONDS));
        start.countDown();

        List<Integer> claimedIds = new ArrayList<>();
        int successfulStockUpdates = 0;
        for (Future<Integer> result : idempotencyResults) {
            claimedIds.add(result.get(10, TimeUnit.SECONDS));
        }
        for (Future<Integer> result : stockResults) {
            successfulStockUpdates += result.get(10, TimeUnit.SECONDS);
        }
        pool.shutdownNow();

        assertEquals(1, claimedIds.stream().distinct().count());
        assertEquals(1, successfulStockUpdates);
        try (Connection connection = mysqlConnection();
             Statement statement = connection.createStatement();
             ResultSet result = statement.executeQuery("SELECT stock FROM middleware_stock WHERE id=1")) {
            assertTrue(result.next());
            assertEquals(0, result.getInt(1));
        }
    }

    @Test
    void mysqlRollbackLeavesNoOutboxRecordAndUniqueKeyRejectsDifferentPayload() throws Exception {
        try (Connection connection = mysqlConnection();
             Statement statement = connection.createStatement()) {
            statement.executeUpdate("""
                    CREATE TABLE IF NOT EXISTS middleware_outbox (
                      id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                      idempotency_key VARCHAR(64) NOT NULL,
                      payload_hash CHAR(64) NOT NULL,
                      UNIQUE KEY uq_outbox_key (idempotency_key)
                    ) ENGINE=InnoDB
                    """);
            statement.executeUpdate("DELETE FROM middleware_outbox");
        }

        try (Connection connection = mysqlConnection();
             PreparedStatement insert = connection.prepareStatement(
                     "INSERT INTO middleware_outbox(idempotency_key, payload_hash) VALUES (?, ?)")) {
            connection.setAutoCommit(false);
            insert.setString(1, "rollback-key");
            insert.setString(2, "hash-a");
            insert.executeUpdate();
            connection.rollback();
        }

        try (Connection connection = mysqlConnection();
             PreparedStatement insert = connection.prepareStatement(
                     "INSERT INTO middleware_outbox(idempotency_key, payload_hash) VALUES (?, ?)")) {
            insert.setString(1, "same-key");
            insert.setString(2, "hash-a");
            assertEquals(1, insert.executeUpdate());
            insert.setString(2, "hash-b");
            assertThrows(java.sql.SQLIntegrityConstraintViolationException.class, () -> {
                insert.executeUpdate();
            });
        }

        try (Connection connection = mysqlConnection();
             Statement statement = connection.createStatement();
             ResultSet result = statement.executeQuery(
                     "SELECT COUNT(*) FROM middleware_outbox WHERE idempotency_key='rollback-key'")) {
            assertTrue(result.next());
            assertEquals(0, result.getInt(1));
        }
    }

    @Test
    void javaDomainSchemasMigrateFreshAndRemainRepeatable() throws Exception {
        List<Path> migrations = findCurrentJavaMigrations();
        assertEquals(9, migrations.size(), migrations.toString());

        for (Path migration : migrations) {
            String jdbcUrl = MYSQL.getJdbcUrl();
            Flyway flyway = Flyway.configure()
                    .dataSource(jdbcUrl, MYSQL.getUsername(), MYSQL.getPassword())
                    .locations("filesystem:" + migration.getParent().toAbsolutePath())
                    .baselineOnMigrate(true)
                    .baselineVersion(MigrationVersion.fromVersion("0"))
                    .cleanDisabled(false)
                    .validateOnMigrate(true)
                    .load();

            flyway.clean();
            flyway.migrate();
            flyway.validate();

            try (Connection connection = DriverManager.getConnection(
                    jdbcUrl, MYSQL.getUsername(), MYSQL.getPassword());
                 Statement statement = connection.createStatement()) {
                assertEquals(1, statement.executeUpdate(
                        "DELETE FROM flyway_schema_history WHERE version IS NULL"));
            }

            flyway.migrate();
            flyway.validate();
            flyway.clean();
        }
    }

    @Test
    void redisCompareDeleteOnlyRemovesTheCurrentOwner() throws Exception {
        String key = "middleware:lock:" + UUID.randomUUID();
        try (RedisWire redis = new RedisWire(REDIS.getHost(), REDIS.getMappedPort(6379))) {
            assertEquals("OK", redis.command("SET", key, "owner-a", "NX", "PX", "30000"));
            assertEquals("0", redis.command(
                    "EVAL",
                    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                    "1", key, "owner-b"));
            assertEquals("owner-a", redis.command("GET", key));
            assertEquals("1", redis.command(
                    "EVAL",
                    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                    "1", key, "owner-a"));
            assertEquals("(nil)", redis.command("GET", key));
        }
    }

    @Test
    void rabbitmqPublishesAndConsumesAnActualMessage() throws Exception {
        ConnectionFactory factory = new ConnectionFactory();
        factory.setHost(RABBITMQ.getHost());
        factory.setPort(RABBITMQ.getAmqpPort());
        factory.setUsername(RABBITMQ.getAdminUsername());
        factory.setPassword(RABBITMQ.getAdminPassword());
        factory.setConnectionTimeout(5_000);

        try (com.rabbitmq.client.Connection connection = factory.newConnection();
             com.rabbitmq.client.Channel channel = connection.createChannel()) {
            String queue = channel.queueDeclare().getQueue();
            byte[] payload = "middleware-message".getBytes(StandardCharsets.UTF_8);
            channel.basicPublish("", queue, null, payload);

            com.rabbitmq.client.GetResponse response = null;
            long deadline = System.nanoTime() + Duration.ofSeconds(5).toNanos();
            while (response == null && System.nanoTime() < deadline) {
                response = channel.basicGet(queue, true);
                if (response == null) {
                    Thread.sleep(50);
                }
            }
            assertNotNull(response);
            assertEquals("middleware-message",
                    new String(response.getBody(), StandardCharsets.UTF_8));
        }
    }

    @Test
    void elasticsearchAcceptsTheSingleEmbeddingContract() throws Exception {
        String index = "middleware_contract_" + UUID.randomUUID().toString().replace("-", "");
        HttpClient client = HttpClient.newHttpClient();
        String mapping = """
                {"mappings":{"properties":{"embedding":{"type":"dense_vector","dims":1024,
                "index":true,"similarity":"cosine"}}}}
                """;
        URI base = URI.create("http://" + ELASTICSEARCH.getHost() + ":"
                + ELASTICSEARCH.getMappedPort(9200));
        try {
            HttpResponse<String> create = client.send(
                    HttpRequest.newBuilder(base.resolve("/" + index))
                            .timeout(Duration.ofSeconds(5))
                            .PUT(HttpRequest.BodyPublishers.ofString(mapping))
                            .header("Content-Type", "application/json")
                            .build(),
                    HttpResponse.BodyHandlers.ofString());
            assertEquals(200, create.statusCode(), create.body());

            HttpResponse<String> read = client.send(
                    HttpRequest.newBuilder(base.resolve("/" + index + "/_mapping"))
                            .timeout(Duration.ofSeconds(5))
                            .GET()
                            .build(),
                    HttpResponse.BodyHandlers.ofString());
            assertEquals(200, read.statusCode(), read.body());
            assertTrue(read.body().contains("\"embedding\""));
            assertTrue(read.body().contains("\"dims\":1024"));
        } finally {
            client.send(
                    HttpRequest.newBuilder(base.resolve("/" + index))
                            .timeout(Duration.ofSeconds(5))
                            .DELETE()
                            .build(),
                    HttpResponse.BodyHandlers.discarding());
        }
    }

    private static Connection mysqlConnection() throws Exception {
        return DriverManager.getConnection(
                MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword());
    }

    private static List<Path> findCurrentJavaMigrations() throws IOException {
        Path root = Path.of(System.getProperty("maven.multiModuleProjectDirectory", "."))
                .toAbsolutePath()
                .normalize();
        while (root != null
                && (!Files.isDirectory(root.resolve("AI_Shop-order"))
                || !Files.isDirectory(root.resolve("AI_Shop-search")))) {
            root = root.getParent();
        }
        if (root == null) {
            throw new IOException("Unable to locate the AI_Shop backend root");
        }
        try (var paths = Files.walk(root)) {
            return paths
                    .filter(Files::isRegularFile)
                    .filter(path -> path.getFileName().toString()
                            .equals("R__current_schema.sql"))
                    .filter(path -> path.toString().contains(
                            "src/main/resources/db/migration"))
                    .sorted()
                    .toList();
        }
    }

    private static final class RedisWire implements AutoCloseable {
        private final Socket socket;
        private final InputStream input;
        private final OutputStream output;

        private RedisWire(String host, int port) throws IOException {
            socket = new Socket();
            socket.connect(new InetSocketAddress(host, port), 3_000);
            input = new BufferedInputStream(socket.getInputStream());
            output = new BufferedOutputStream(socket.getOutputStream());
        }

        private String command(String... args) throws IOException {
            output.write(('*' + String.valueOf(args.length) + "\r\n").getBytes(StandardCharsets.UTF_8));
            for (String arg : args) {
                byte[] bytes = arg.getBytes(StandardCharsets.UTF_8);
                output.write(('$' + String.valueOf(bytes.length) + "\r\n").getBytes(StandardCharsets.UTF_8));
                output.write(bytes);
                output.write("\r\n".getBytes(StandardCharsets.UTF_8));
            }
            output.flush();
            return readReply(input);
        }

        private static String readReply(InputStream input) throws IOException {
            int prefix = input.read();
            if (prefix == -1) {
                throw new IOException("Redis closed the connection");
            }
            String line = readLine(input);
            return switch (prefix) {
                case '+' -> line;
                case ':' -> line;
                case '-' -> throw new IOException(line);
                case '$' -> {
                    int length = Integer.parseInt(line);
                    if (length < 0) {
                        yield "(nil)";
                    }
                    byte[] body = input.readNBytes(length);
                    input.read();
                    input.read();
                    yield new String(body, StandardCharsets.UTF_8);
                }
                default -> throw new IOException("Unsupported Redis response: " + (char) prefix);
            };
        }

        private static String readLine(InputStream input) throws IOException {
            ByteArrayOutputStream line = new ByteArrayOutputStream();
            int current;
            int previous = -1;
            while ((current = input.read()) != -1) {
                if (previous == '\r' && current == '\n') {
                    byte[] bytes = line.toByteArray();
                    return new String(bytes, 0, bytes.length - 1, StandardCharsets.UTF_8);
                }
                line.write(current);
                previous = current;
            }
            throw new IOException("Redis response line ended unexpectedly");
        }

        @Override
        public void close() throws IOException {
            socket.close();
        }
    }
}
