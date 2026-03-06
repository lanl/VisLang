# TODO

- [ ] Pass field selection results to the baseline spec prompt. Currently only the Draco path benefits from field selection (indirectly, via recommendations). The baseline LLM must re-derive which fields are relevant from the full schema, making the comparison not purely "with Draco vs. without." Feed `fields`, `mark_hint`, and `channel_hints` into the baseline prompt so both paths start from the same field selection.
