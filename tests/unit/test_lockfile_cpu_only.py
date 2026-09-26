"""CA-23 (uv.lock part): the resolved dependency tree contains no CUDA/GPU packages."""

import tomllib
from pathlib import Path
from typing import Any

import pytest

LOCKFILE = Path(__file__).resolve().parents[2] / "uv.lock"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
FORBIDDEN_PREFIXES = ("nvidia-", "cuda-")
FORBIDDEN_NAMES = {"triton", "onnxruntime-gpu"}
CPU_ONLY_PACKAGES = {"torch", "torchvision"}


@pytest.fixture(scope="module")
def packages() -> list[dict[str, Any]]:
    with LOCKFILE.open("rb") as fh:
        lock = tomllib.load(fh)
    result: list[dict[str, Any]] = lock["package"]
    return result


def test_lockfile_has_no_gpu_packages(packages: list[dict[str, Any]]) -> None:
    gpu = [
        p["name"]
        for p in packages
        if p["name"].startswith(FORBIDDEN_PREFIXES) or p["name"] in FORBIDDEN_NAMES
    ]

    assert gpu == []


def test_torch_packages_come_from_pytorch_cpu_index(packages: list[dict[str, Any]]) -> None:
    torch_like = [p for p in packages if p["name"] in CPU_ONLY_PACKAGES]

    assert {p["name"] for p in torch_like} == CPU_ONLY_PACKAGES
    for package in torch_like:
        assert package["source"] == {"registry": PYTORCH_CPU_INDEX}, package["name"]
        assert "+cu" not in package["version"], package["name"]
