package com.aishop.integration;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@Testcontainers
class TransactionPersistenceIT {

    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.0.36")
            .withDatabaseName("aishop_transaction_it")
            .withUsername("aishop")
            .withPassword("aishop");

    @BeforeEach
    void resetSchema() throws Exception {
        try (Connection connection = connection();
             Statement statement = connection.createStatement()) {
            statement.execute("DROP TABLE IF EXISTS stock_change_record");
            statement.execute("DROP TABLE IF EXISTS sku_stock");
            statement.execute("DROP TABLE IF EXISTS pay_trade_record");
            statement.execute("DROP TABLE IF EXISTS order_request_idempotency");
            statement.execute("""
                    CREATE TABLE order_request_idempotency (
                      id BIGINT AUTO_INCREMENT PRIMARY KEY,
                      user_id VARCHAR(15) NOT NULL,
                      command_type VARCHAR(32) NOT NULL,
                      idempotency_key VARCHAR(64) NOT NULL,
                      request_hash CHAR(64) NOT NULL,
                      status VARCHAR(16) NOT NULL DEFAULT 'PROCESSING',
                      response_json MEDIUMTEXT NULL,
                      create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                      CONSTRAINT uk_order_request_idempotency
                        UNIQUE (user_id, command_type, idempotency_key)
                    ) ENGINE=InnoDB
                    """);
            statement.execute("""
                    CREATE TABLE pay_trade_record (
                      trade_id VARCHAR(33) NOT NULL PRIMARY KEY,
                      order_id VARCHAR(32) NOT NULL,
                      user_id VARCHAR(15) NOT NULL,
                      pay_order_id VARCHAR(32) NOT NULL,
                      channel_order_id VARCHAR(50) NULL,
                      pay_amount DECIMAL(10,2) NOT NULL,
                      trade_status TINYINT NOT NULL DEFAULT 0,
                      pay_time DATETIME NULL,
                      create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      CONSTRAINT uk_pay_trade_pay_order UNIQUE (pay_order_id)
                    ) ENGINE=InnoDB
                    """);
            statement.execute("""
                    CREATE TABLE sku_stock (
                      product_id VARCHAR(15) NOT NULL,
                      property_value_id_hash VARCHAR(32) NOT NULL,
                      stock INT NOT NULL,
                      PRIMARY KEY (product_id, property_value_id_hash)
                    ) ENGINE=InnoDB
                    """);
            statement.execute("""
                    CREATE TABLE stock_change_record (
                      business_key VARCHAR(96) NOT NULL PRIMARY KEY,
                      change_type VARCHAR(32) NOT NULL,
                      product_id VARCHAR(15) NOT NULL,
                      property_value_id_hash VARCHAR(32) NOT NULL,
                      change_amount INT NOT NULL,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB
                    """);
        }
    }

    @Test
    void concurrentOrderRequestsPersistOneCommandAndExposePayloadConflict() throws Exception {
        int workers = 10;
        ExecutorService pool = Executors.newFixedThreadPool(workers);
        CountDownLatch ready = new CountDownLatch(workers);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Integer>> results = new ArrayList<>();
        for (int index = 0; index < workers; index++) {
            results.add(pool.submit(() -> {
                ready.countDown();
                start.await();
                try (Connection connection = connection();
                     PreparedStatement insert = connection.prepareStatement("""
                             INSERT IGNORE INTO order_request_idempotency
                               (user_id, command_type, idempotency_key, request_hash)
                             VALUES ('u1', 'POST_ORDER', 'same-order-request', ?)
                             """)) {
                    insert.setString(1, "hash-a");
                    return insert.executeUpdate();
                }
            }));
        }
        assertTrue(ready.await(10, TimeUnit.SECONDS));
        start.countDown();
        int inserted = 0;
        for (Future<Integer> result : results) {
            inserted += result.get(10, TimeUnit.SECONDS);
        }
        pool.shutdownNow();

        assertEquals(1, inserted);
        try (Connection connection = connection();
             PreparedStatement conflict = connection.prepareStatement("""
                     INSERT IGNORE INTO order_request_idempotency
                       (user_id, command_type, idempotency_key, request_hash)
                     VALUES ('u1', 'POST_ORDER', 'same-order-request', 'hash-b')
                     """)) {
            assertEquals(0, conflict.executeUpdate(),
                    "the unique command key must not accept a second payload");
        }
        try (Connection connection = connection();
             PreparedStatement select = connection.prepareStatement("""
                     SELECT request_hash, COUNT(*) OVER () AS row_count
                       FROM order_request_idempotency
                      WHERE user_id='u1' AND command_type='POST_ORDER'
                        AND idempotency_key='same-order-request'
                     """)) {
            try (ResultSet result = select.executeQuery()) {
                assertTrue(result.next());
                assertEquals("hash-a", result.getString("request_hash"));
                assertEquals(1, result.getInt("row_count"));
            }
        }
    }

    @Test
    void concurrentPaymentCallbacksApplyOnePendingToSuccessTransition() throws Exception {
        try (Connection connection = connection();
             Statement statement = connection.createStatement()) {
            statement.executeUpdate("""
                    INSERT INTO pay_trade_record
                      (trade_id, order_id, user_id, pay_order_id, pay_amount, trade_status)
                    VALUES ('trade-1', 'order-1', 'u1', 'pay-1', 99.00, 0)
                    """);
        }

        int workers = 12;
        ExecutorService pool = Executors.newFixedThreadPool(workers);
        CountDownLatch ready = new CountDownLatch(workers);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Integer>> results = new ArrayList<>();
        for (int index = 0; index < workers; index++) {
            int callback = index;
            results.add(pool.submit(() -> {
                ready.countDown();
                start.await();
                try (Connection connection = connection();
                     PreparedStatement update = connection.prepareStatement("""
                             UPDATE pay_trade_record
                                SET trade_status=1, channel_order_id=?, pay_time=NOW()
                              WHERE pay_order_id='pay-1' AND trade_status=0
                             """)) {
                    update.setString(1, "channel-" + callback);
                    return update.executeUpdate();
                }
            }));
        }
        assertTrue(ready.await(10, TimeUnit.SECONDS));
        start.countDown();
        int applied = 0;
        for (Future<Integer> result : results) {
            applied += result.get(10, TimeUnit.SECONDS);
        }
        pool.shutdownNow();

        assertEquals(1, applied);
        assertEquals(1, scalarInt("SELECT trade_status FROM pay_trade_record WHERE pay_order_id='pay-1'"));
        assertEquals(0, executeUpdate("""
                UPDATE pay_trade_record SET trade_status=1
                 WHERE pay_order_id='pay-1' AND trade_status=0
                """));
    }

    @Test
    void concurrentRefundRedeliveryRestoresStockExactlyOnce() throws Exception {
        executeUpdate("INSERT INTO sku_stock VALUES ('p1', 'sku1', 0)");
        int workers = 10;
        ExecutorService pool = Executors.newFixedThreadPool(workers);
        CountDownLatch ready = new CountDownLatch(workers);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Integer>> results = new ArrayList<>();
        for (int index = 0; index < workers; index++) {
            results.add(pool.submit(() -> {
                ready.countDown();
                start.await();
                try (Connection connection = connection()) {
                    connection.setAutoCommit(false);
                    try (PreparedStatement ledger = connection.prepareStatement("""
                            INSERT IGNORE INTO stock_change_record
                              (business_key, change_type, product_id, property_value_id_hash, change_amount)
                            VALUES ('refund:r1', 'REFUND_RESTORE', 'p1', 'sku1', 2)
                            """)) {
                        int claimed = ledger.executeUpdate();
                        if (claimed == 1) {
                            try (PreparedStatement stock = connection.prepareStatement("""
                                    UPDATE sku_stock SET stock=stock+2
                                     WHERE product_id='p1' AND property_value_id_hash='sku1'
                                    """)) {
                                assertEquals(1, stock.executeUpdate());
                            }
                        }
                        connection.commit();
                        return claimed;
                    } catch (Exception exc) {
                        connection.rollback();
                        throw exc;
                    }
                }
            }));
        }
        assertTrue(ready.await(10, TimeUnit.SECONDS));
        start.countDown();
        int claimed = 0;
        for (Future<Integer> result : results) {
            claimed += result.get(10, TimeUnit.SECONDS);
        }
        pool.shutdownNow();

        assertEquals(1, claimed);
        assertEquals(2, scalarInt("SELECT stock FROM sku_stock WHERE product_id='p1' AND property_value_id_hash='sku1'"));
        assertEquals(1, scalarInt("SELECT COUNT(*) FROM stock_change_record WHERE business_key='refund:r1'"));
    }

    private static Connection connection() throws SQLException {
        return DriverManager.getConnection(
                MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword());
    }

    private static int executeUpdate(String sql) throws SQLException {
        try (Connection connection = connection();
             Statement statement = connection.createStatement()) {
            return statement.executeUpdate(sql);
        }
    }

    private static int scalarInt(String sql) throws SQLException {
        try (Connection connection = connection();
             Statement statement = connection.createStatement();
             ResultSet result = statement.executeQuery(sql)) {
            assertTrue(result.next());
            return result.getInt(1);
        }
    }
}
