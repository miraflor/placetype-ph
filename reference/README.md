# Reference taxonomies

Generated `nodes.parquet` files live under versioned directories such as:

- `reference/psic_rev5/nodes.parquet`
- `reference/pcpc_2002/nodes.parquet`
- `reference/pscc_2022/nodes.parquet`

They are generated from Philippine Statistics Authority reference material and are
intentionally excluded from Git by default so the repository does not silently
freeze a classification revision. `placetype taxonomy fetch ...` records the version
explicitly and recreates the machine-readable tree.

The software is MIT licensed. PSA source/reference material retains its own terms.
PSA website content is generally stated as CC BY 4.0 unless otherwise noted; verify
the terms attached to any downloaded source file before redistribution.
