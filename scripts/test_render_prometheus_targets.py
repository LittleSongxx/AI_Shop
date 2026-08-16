import json
import os
import stat
from pathlib import Path

from scripts.render_prometheus_targets import render


def test_render_uses_dynamic_runtime_ports(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.env"
    output = tmp_path / "observability"
    runtime.write_text(
        "\n".join(
            [
                "GATEWAY_PORT=18080",
                "CART_PORT=18086",
                "COUPON_PORT=18089",
                "AGENT_PORT=17050",
                "AGENT_WORKER_METRICS_PORT=17051",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    render(runtime, output)

    java = json.loads((output / "java.json").read_text(encoding="utf-8"))
    agent = json.loads((output / "agent.json").read_text(encoding="utf-8"))
    worker = json.loads((output / "worker.json").read_text(encoding="utf-8"))
    targets = {group["labels"]["service"]: group["targets"][0] for group in java}

    assert targets["aishop-gateway"] == "host.docker.internal:18080"
    assert targets["aishop-cart"] == "host.docker.internal:18086"
    assert targets["aishop-coupon"] == "host.docker.internal:18089"
    assert agent[0]["targets"] == ["host.docker.internal:17050"]
    assert worker[0]["targets"] == ["host.docker.internal:17051"]


def test_rendered_targets_are_container_readable_under_restrictive_umask(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime.env"
    output = tmp_path / "observability"
    runtime.write_text("GATEWAY_PORT=18080\n", encoding="utf-8")
    previous = os.umask(0o077)
    try:
        render(runtime, output)
    finally:
        os.umask(previous)

    for path in output.glob("*.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
