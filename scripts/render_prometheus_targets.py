"""Render Prometheus file-SD targets from the current local runtime ports."""

from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path


JAVA_TARGETS = (
    ("GATEWAY_PORT", "aishop-gateway", "gateway"),
    ("USER_PORT", "aishop-user", "business"),
    ("PRODUCT_PORT", "aishop-product", "business"),
    ("STOCK_PORT", "aishop-stock", "business"),
    ("CART_PORT", "aishop-cart", "business"),
    ("ORDER_PORT", "aishop-order", "business"),
    ("PAY_PORT", "aishop-pay", "business"),
    ("COUPON_PORT", "aishop-coupon", "business"),
    ("SEARCH_PORT", "aishop-search", "business"),
    ("ADMIN_PORT", "aishop-admin", "business"),
)

DEFAULT_PORTS = {
    "GATEWAY_PORT": 8080,
    "USER_PORT": 8105,
    "PRODUCT_PORT": 8099,
    "STOCK_PORT": 8102,
    "CART_PORT": 8084,
    "ORDER_PORT": 8093,
    "PAY_PORT": 8096,
    "COUPON_PORT": 8087,
    "SEARCH_PORT": 8108,
    "ADMIN_PORT": 8111,
    "AGENT_PORT": 7050,
    "AGENT_WORKER_METRICS_PORT": 7051,
}


def load_runtime_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            try:
                parsed = shlex.split(raw_value, comments=False)
            except ValueError as exc:
                raise ValueError(f"无法解析运行配置 {path}: {raw_line}") from exc
            values[key.strip()] = parsed[0] if parsed else ""
    for key in DEFAULT_PORTS:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def port(values: dict[str, str], key: str) -> int:
    raw = values.get(key) or str(DEFAULT_PORTS[key])
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} 不是有效端口: {raw!r}") from exc
    if not 1 <= value <= 65535:
        raise ValueError(f"{key} 不在 1..65535 范围内: {value}")
    return value


def target(port_number: int, service: str, tier: str) -> dict[str, object]:
    return {
        "targets": [f"host.docker.internal:{port_number}"],
        "labels": {"service": service, "tier": tier},
    }


def render(runtime_env: Path, output_dir: Path) -> None:
    values = load_runtime_values(runtime_env)
    java = [
        target(port(values, key), service, tier)
        for key, service, tier in JAVA_TARGETS
    ]
    agent = [target(port(values, "AGENT_PORT"), "aishop-agent", "ai")]
    worker = [
        target(
            port(values, "AGENT_WORKER_METRICS_PORT"),
            "aishop-agent-worker",
            "ai",
        )
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "java.json": java,
        "agent.json": agent,
        "worker.json": worker,
    }
    for filename, content in files.items():
        temporary = output_dir / f".{filename}.tmp"
        temporary.write_text(
            json.dumps(content, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        # file-SD only contains local endpoints and labels. Prometheus runs as
        # `nobody` in the official image, so these files must remain readable
        # even when the caller uses a restrictive umask for runtime secrets.
        temporary.chmod(0o644)
        temporary.replace(output_dir / filename)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    render(args.runtime_env, args.output_dir)


if __name__ == "__main__":
    main()
