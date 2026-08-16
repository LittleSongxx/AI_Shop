FROM apache/seata-server:2.5.0

# The upstream image does not ship the MySQL driver.  Fetch the pinned driver
# during the image build so a clean checkout does not depend on an untracked
# `deploy/seata/jdbc` binary being present.
ADD --checksum=sha256:d77962877d010777cff997015da90ee689f0f4bb76848340e1488f2b83332af5 \
    https://repo.maven.apache.org/maven2/com/mysql/mysql-connector-j/8.4.0/mysql-connector-j-8.4.0.jar \
    /seata-server/libs/mysql-connector-j-8.4.0.jar

# validate() uses the TCCL and the Seata launcher also checks /lib/jdbc.
RUN mkdir -p /lib/jdbc && cp /seata-server/libs/mysql-connector-j-8.4.0.jar /lib/jdbc/mysql-connector-j-8.4.0.jar

COPY application.yml /seata-server/resources/application.yml
