package com.aishop.config;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class ProductionSafetyValidatorTest {

    @AfterEach
    void clearDevProperty() {
        System.clearProperty("dev");
    }

    @Test
    void productionRejectsMissingDedicatedOpsToken() {
        ProductionSafetyValidator validator = validator(true);
        ReflectionTestUtils.setField(validator, "internalOpsToken", "");

        assertThrows(IllegalStateException.class, validator::validate);
    }

    @Test
    void productionRejectsReusedInternalToken() {
        ProductionSafetyValidator validator = validator(true);
        ReflectionTestUtils.setField(validator, "internalOpsToken", "internal-secret");

        assertThrows(IllegalStateException.class, validator::validate);
    }

    @Test
    void productionAcceptsIndependentStrongTokens() {
        assertDoesNotThrow(() -> validator(true).validate());
    }

    @Test
    void productionRejectsRootDatabaseIdentity() {
        ProductionSafetyValidator validator = validator(true);
        ReflectionTestUtils.setField(validator, "databaseUsername", "root");

        assertThrows(IllegalStateException.class, validator::validate);
    }

    @Test
    void productionRejectsMissingDatabasePassword() {
        ProductionSafetyValidator validator = validator(true);
        ReflectionTestUtils.setField(validator, "databasePassword", "");

        assertThrows(IllegalStateException.class, validator::validate);
    }

    @Test
    void productionRejectsReusedFlywayIdentity() {
        ProductionSafetyValidator validator = validator(true);
        ReflectionTestUtils.setField(validator, "flywayUsername", "aishop");

        assertThrows(IllegalStateException.class, validator::validate);
    }

    @Test
    void productionAllowsFlywayToBeDisabledAfterExternalMigration() {
        ProductionSafetyValidator validator = validator(true);
        ReflectionTestUtils.setField(validator, "flywayEnabled", false);
        ReflectionTestUtils.setField(validator, "flywayUsername", "");
        ReflectionTestUtils.setField(validator, "flywayPassword", "");

        assertDoesNotThrow(validator::validate);
    }

    @Test
    void developmentModeDoesNotRequireProductionSecrets() {
        assertDoesNotThrow(() -> validator(false).validate());
    }

    private static ProductionSafetyValidator validator(boolean productionReady) {
        ProductionSafetyValidator validator = new ProductionSafetyValidator();
        ReflectionTestUtils.setField(validator, "productionReady", productionReady);
        ReflectionTestUtils.setField(validator, "internalToken", "internal-secret");
        ReflectionTestUtils.setField(validator, "internalOpsToken", "ops-secret");
        ReflectionTestUtils.setField(validator, "adminPassword", "strong-admin-password");
        ReflectionTestUtils.setField(validator, "devLoginBypass", false);
        ReflectionTestUtils.setField(validator, "projectFolder", "/data/aishop/upload/");
        ReflectionTestUtils.setField(validator, "databaseUsername", "aishop");
        ReflectionTestUtils.setField(validator, "databasePassword", "strong-db-password");
        ReflectionTestUtils.setField(validator, "flywayEnabled", true);
        ReflectionTestUtils.setField(validator, "flywayUsername", "aishop_flyway");
        ReflectionTestUtils.setField(validator, "flywayPassword", "strong-flyway-password");
        return validator;
    }
}
