# The evidence model

Evidence answers **how we know something is true**. It is the third artifact,
kept deliberately separate from the WorkGraph and the Outcome Contract.

## Epistemic labels

Every fact carries one, and the label is a required field rather than a UI
convention:

| Label | Meaning |
| --- | --- |
| `OBSERVED` | Read directly from a source: an API response, a row, a document field |
| `INFERRED` | Derived by computation or by a model; carries provenance and confidence |
| `RECOMMENDED` | A proposal for a human; never executable without approval |

**Only `OBSERVED` facts can satisfy a `STRONG` assertion.** Making that a
schema-level constraint is what prevents the slow drift into fabricated
evidence.

## Content addressing

An evidence record's id is derived from the hash of its content, so writing
the same fact twice is a no-op and altering a stored fact is impossible
without changing its address. Payloads are canonical JSON — sorted keys, no
whitespace, decimals as strings — so the same fact produces the same address
on every machine.

```
.verity/evidence/objects/2f/2f06e3a4….json
```

## Provenance

Document fields carry where they physically came from:

```json
{
  "source_ref": "sha256:9c1f…",
  "page": 1,
  "bbox": [486.6, 303.0, 540.0, 315.0],
  "field": "total",
  "method": "label-proximity:total due",
  "confidence": 0.97
}
```

The bounding box is what lets a failure highlight the exact region of an
invoice rather than gesturing at the file. The `method` records which label the
value was found next to — and a change of anchor between runs is the drift
signal for a template that moved.

## Redaction

Redaction runs **before** anything is persisted and fails closed: a field that
looks like a secret is redacted even when the detector is unsure. Losing a
value from an evidence record is recoverable; writing a credential to disk is
not.

Detection covers sensitive field names, JWTs, bearer tokens, private key
headers, vendor-prefixed API keys, and Luhn-valid card numbers. Long numbers
that are *not* Luhn-valid are preserved, because destroying a legitimate
invoice reference would make the evidence useless.

## Bundles

A bundle is the portable output of one verification:

```
bundle/
  report.json       the verdict, assertions, facts, divergence
  evidence.json     the records cited
  manifest.json     entries plus a merkle root
  objects/…         every cited evidence object
```

```bash
verity verify --contract c.yaml --live --bundle ./evidence
```

`check_bundle()` verifies both that every object hashes to its recorded digest
and that the manifest's merkle root matches its own entries — so neither
editing an object nor editing the manifest passes. Leaves are sorted before
hashing, so two runs that gather the same evidence in a different order produce
the same root.

This bundle is what CI uploads as an artifact and what a person opens three
weeks later to answer "how do we know that?".
