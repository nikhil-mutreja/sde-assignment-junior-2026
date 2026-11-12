"""Schema models and loaders for workflow lead definitions and disposition catalogues."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from solution.utils.files import normalise_header


class FieldDefinition(BaseModel):
    """Definition of a single field in a workflow lead schema."""

    name: str
    type: str  # string, phone, date, decimal, enum
    required: bool = False
    pii: bool = False
    reference: bool = False
    aliases: List[str] = Field(default_factory=list)
    values: List[str] = Field(default_factory=list)
    default: Optional[str] = None

    def get_normalised_aliases(self) -> List[str]:
        """Return normalised representations of the field name and its aliases."""
        return [normalise_header(self.name)] + [normalise_header(a) for a in self.aliases]


class WorkflowSchema(BaseModel):
    """Lead schema definition for a specific workflow."""

    workflow_id: str
    description: Optional[str] = None
    fields: List[FieldDefinition]

    def get_reference_field(self) -> FieldDefinition:
        """Return the field marked as reference=true."""
        for f in self.fields:
            if f.reference:
                return f
        raise ValueError(f"No reference field defined in schema for {self.workflow_id}")

    def map_headers(self, input_headers: List[str]) -> Dict[str, str]:
        """Map raw CSV headers to schema field names.

        Returns dict: input_header -> schema_field_name.
        Ignores columns that match no field in the schema.
        """
        # Build lookup table: normalised_alias -> schema_field_name
        lookup: Dict[str, str] = {}
        for f in self.fields:
            for alias_norm in f.get_normalised_aliases():
                lookup[alias_norm] = f.name

        mapping: Dict[str, str] = {}
        for raw_h in input_headers:
            norm_h = normalise_header(raw_h)
            if norm_h in lookup:
                mapping[raw_h] = lookup[norm_h]
        return mapping


class DispositionCatalogue:
    """Catalogue of allowed call dispositions per workflow."""

    def __init__(self, entries: Dict[Tuple[str, str], Tuple[str, bool, bool]]):
        # Key: (workflow_id, code) -> (label, is_terminal, is_success)
        self._entries = entries

    @classmethod
    def from_csv(cls, csv_path: Path) -> DispositionCatalogue:
        """Load disposition catalogue from dispositions.csv."""
        entries: Dict[Tuple[str, str], Tuple[str, bool, bool]] = {}
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                wf_id = row["workflow_id"].strip()
                code = row["code"].strip()
                label = row["label"].strip()
                is_terminal = row["is_terminal"].strip().lower() == "true"
                is_success = row["is_success"].strip().lower() == "true"
                entries[(wf_id, code)] = (label, is_terminal, is_success)
        return cls(entries)

    def is_valid(self, workflow_id: str, code: str) -> bool:
        """Check if a disposition code is valid for the workflow."""
        return (workflow_id, code) in self._entries

    def is_terminal(self, workflow_id: str, code: str) -> bool:
        """Check if a disposition code is terminal."""
        entry = self._entries.get((workflow_id, code))
        return entry[1] if entry else False

    def is_success(self, workflow_id: str, code: str) -> bool:
        """Check if a disposition code is successful."""
        entry = self._entries.get((workflow_id, code))
        return entry[2] if entry else False


def load_workflow_schemas(schemas_dir: Path) -> Dict[str, WorkflowSchema]:
    """Load all JSON workflow schemas from the schemas directory."""
    schemas: Dict[str, WorkflowSchema] = {}
    for json_file in schemas_dir.glob("*.json"):
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            schema = WorkflowSchema.model_validate(data)
            schemas[schema.workflow_id] = schema
    return schemas
