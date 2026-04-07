from __future__ import annotations

from codeingme.contracts import TestSpec


class AcceptanceTestGenerator:
    def generate(self, app_name: str) -> list[TestSpec]:
        test_path = f"tests_generated/test_{app_name}_demo.py"
        return [
            TestSpec(
                name=f"{app_name}_contract_test",
                description=f"Verify the {app_name} contract is satisfied",
                expected_state="green",
                path=test_path,
            ),
            TestSpec(
                name=f"{app_name}_rule_test",
                description=f"Verify the {app_name} backend rules remain visible in the API payload",
                expected_state="green",
                path=test_path,
            ),
        ]
