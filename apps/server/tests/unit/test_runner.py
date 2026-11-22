from deepseek_harness.errors import TransportClosedError

from src.agent.runner import runtime_crash

BOOT_FAILURE = (
    """DeepSeek Harness runtime stdout closed
exit code: 1
stderr tail:
Error: dsh: host preparation failed: No usable native binding found for node-addon (auto)
    at boot (file:///snapshot/node/node_modules/@deepseek-ai/dsh-app-boot/lib/index.js:5329:9)
  [cause]: Error: No usable native binding found for node-addon (auto)
"""
    + "    at frame (file:///snapshot/node/lib/index.js:1:1)\n" * 400
)


def test_runtime_failure_is_summarized_to_its_cause() -> None:
    crash = runtime_crash(TransportClosedError(BOOT_FAILURE))

    assert str(crash) == (
        "DeepSeek Harness runtime stdout closed; exit code: 1; "
        "Error: dsh: host preparation failed: No usable native binding found for node-addon (auto)"
    )


def test_runtime_failure_summary_is_bounded() -> None:
    crash = runtime_crash(TransportClosedError("x" * 100_000))

    assert len(str(crash)) == 1000


def test_runtime_failure_without_a_message_names_its_type() -> None:
    assert str(runtime_crash(TransportClosedError())) == "TransportClosedError"
