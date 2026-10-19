from __future__ import annotations


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def same_successful_outputs(left, right) -> bool:
    return (
        left.current_status == right.current_status == "succeeded"
        and {key: value.output for key, value in left.task_results.items()}
        == {key: value.output for key, value in right.task_results.items()}
    )

