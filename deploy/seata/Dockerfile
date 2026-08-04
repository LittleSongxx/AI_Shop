FROM apache/seata-server:2.2.0

# The upstream image does not ship the MySQL driver.  Fetch the pinned driver
# during the image build so a clean checkout does not depend on an untracked
# `deploy/seata/jdbc` binary being present.
ADD --checksum=sha256:e2a3b2fc726a1ac64e998585db86b30fa8bf3f706195b78bb77c5f99bf877bd9 \
    https://repo.maven.apache.org/maven2/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar \
    /seata-server/libs/mysql-connector-j-8.0.33.jar

# validate() uses the TCCL and the Seata launcher also checks /lib/jdbc.
RUN mkdir -p /lib/jdbc && cp /seata-server/libs/mysql-connector-j-8.0.33.jar /lib/jdbc/mysql-connector-j-8.0.33.jar

COPY application.yml /seata-server/resources/application.yml
