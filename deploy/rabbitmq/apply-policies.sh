#!/bin/sh
set -eu

POLICY_PATTERN='^(rushing\.delay\.queue|pay\.(timeout|logistics|confirm)\.delay\.queue|user\.tempban\.delay\.queue|refund\.(stock|result)\.queue|user\.growth\.queue|rag\.queue|agent\.(support\.high|faq\.fast|shopping\.low)|visual\.index\.queue|.*\.retry\.[1-3])$'

exec rabbitmqadmin \
    --host rabbitmq \
    --port 15672 \
    --username "${RABBIT_USER:-aishop}" \
    --password "${RABBIT_PASSWORD:-aishop}" \
    --vhost "${RABBIT_VHOST:-/}" \
    --non-interactive \
    policies declare \
    --name aishop-quorum-dead-lettering \
    --pattern "$POLICY_PATTERN" \
    --apply-to quorum_queues \
    --priority 10 \
    --definition '{"dead-letter-strategy":"at-least-once","overflow":"reject-publish"}'
