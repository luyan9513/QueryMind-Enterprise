"""Dataset loader for QueryMind SQL evaluation."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import SqlTestCase
from .validation import EvaluationDatasetValidator


@lru_cache(maxsize=1)
def _default_validator() -> EvaluationDatasetValidator:
    return EvaluationDatasetValidator.default()


class EvaluationDataset:
    """Collection of SQL evaluation test cases."""

    def __init__(self, name: str, test_cases: List[SqlTestCase], description: str = ""):
        self.name = name
        self.test_cases = test_cases
        self.description = description

    @classmethod
    def from_yaml(cls, path: str | Path) -> "EvaluationDataset":
        return cls._from_yaml(Path(path).resolve(), ancestry=())

    @classmethod
    def _from_yaml(
        cls,
        source_path: Path,
        *,
        ancestry: tuple[Path, ...],
    ) -> "EvaluationDataset":
        try:
            import yaml
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required to load YAML datasets. Install pyyaml or use JSON."
            ) from exc

        if source_path in ancestry:
            chain = " -> ".join(str(item) for item in (*ancestry, source_path))
            raise ValueError(f"Dataset include cycle detected: {chain}")
        with open(source_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        dataset = cls._from_dict(data, source=str(source_path))
        root = data.get("dataset", data) if isinstance(data, dict) else {}
        include_paths = root.get("includes", []) if isinstance(root, dict) else []
        if not isinstance(include_paths, list) or any(
            not isinstance(item, str) for item in include_paths
        ):
            raise ValueError("dataset.includes must be a list of relative paths")
        seen_ids = {case.id for case in dataset.test_cases}
        for include_path in include_paths:
            resolved = (source_path.parent / include_path).resolve()
            included = cls._from_yaml(
                resolved,
                ancestry=(*ancestry, source_path),
            )
            duplicates = seen_ids.intersection(case.id for case in included.test_cases)
            if duplicates:
                raise ValueError(
                    "Duplicate test case IDs across dataset includes: "
                    + ", ".join(sorted(duplicates))
                )
            dataset.test_cases.extend(included.test_cases)
            seen_ids.update(case.id for case in included.test_cases)
        split_ranges = root.get("split_ranges", {}) if isinstance(root, dict) else {}
        if split_ranges:
            if not isinstance(split_ranges, dict):
                raise ValueError("dataset.split_ranges must be a mapping")
            for split_name, bounds in split_ranges.items():
                if not isinstance(bounds, dict) or not bounds.get("start") or not bounds.get("end"):
                    raise ValueError(
                        "Each dataset split range requires start and end case IDs"
                    )
                start = str(bounds["start"])
                end = str(bounds["end"])
                for case in dataset.test_cases:
                    if start <= case.id <= end:
                        if case.metadata.get("benchmark_split"):
                            raise ValueError(f"Case {case.id} belongs to multiple splits")
                        case.metadata["benchmark_split"] = str(split_name)
        feature_mode = (
            root.get("sql_contract_feature_mode") if isinstance(root, dict) else None
        )
        if feature_mode is not None:
            if feature_mode not in {"blocking", "advisory"}:
                raise ValueError(
                    "dataset.sql_contract_feature_mode must be blocking or advisory"
                )
            for case in dataset.test_cases:
                if case.expected_sql_contract is not None:
                    case.expected_sql_contract.feature_requirement_mode = feature_mode
        return dataset

    @classmethod
    def from_json(cls, path: str | Path) -> "EvaluationDataset":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls._from_dict(data, source=str(path))

    @classmethod
    def _from_dict(cls, data: Dict[str, Any], *, source: Optional[str] = None) -> "EvaluationDataset":
        _default_validator().validate(data, source=source)
        dataset = data.get("dataset", data)
        name = dataset.get("name", "Unnamed Dataset")
        description = dataset.get("description", "")

        test_cases = [cls._parse_test_case(item) for item in dataset.get("test_cases", [])]
        return cls(name=name, test_cases=test_cases, description=description)

    @staticmethod
    def _parse_test_case(data: Dict[str, Any]) -> SqlTestCase:
        return SqlTestCase.model_validate(data)

    def save_yaml(self, path: str | Path) -> None:
        try:
            import yaml
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required to save YAML datasets. Install pyyaml or use JSON."
            ) from exc

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self._to_dict(), f, sort_keys=False, allow_unicode=True)

    def save_json(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._to_dict(), f, indent=2, ensure_ascii=False)

    def _to_dict(self) -> Dict[str, Any]:
        return {
            "dataset": {
                "name": self.name,
                "description": self.description,
                "test_cases": [case.model_dump(mode="json") for case in self.test_cases],
            }
        }

    def filter_by_metadata(self, **kwargs: Any) -> "EvaluationDataset":
        filtered = [
            case
            for case in self.test_cases
            if all(case.metadata.get(key) == value for key, value in kwargs.items())
        ]
        return EvaluationDataset(
            name=f"{self.name} (filtered)",
            test_cases=filtered,
            description=f"Filtered from: {self.description}",
        )

    def group_by_database(self) -> Dict[str, List[SqlTestCase]]:
        grouped: Dict[str, List[SqlTestCase]] = {}
        for case in self.test_cases:
            grouped.setdefault(case.database_id, []).append(case)
        return grouped

    def __len__(self) -> int:
        return len(self.test_cases)

    def __iter__(self):
        return iter(self.test_cases)

    def __repr__(self) -> str:
        return f"EvaluationDataset(name='{self.name}', test_cases={len(self.test_cases)})"
